"""GStreamer preview and recording helpers."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402


Gst.init(None)


class VideoStreamClient(QObject):
    """Receive preview frames from either RTP or a local camera, then record MP4 files."""

    frame_ready = Signal(QImage)
    preview_state_changed = Signal(str)
    preview_error = Signal(str)
    recording_state_changed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._preview_pipeline: Gst.Pipeline | None = None
        self._preview_bus: Gst.Bus | None = None
        self._preview_sink: Gst.Element | None = None

        self._record_pipeline: Gst.Pipeline | None = None
        self._record_bus: Gst.Bus | None = None
        self._record_appsrc: Gst.Element | None = None

        self._latest_image = QImage()
        self._latest_frame_bytes = b""
        self._frame_width = 0
        self._frame_height = 0
        self._frame_index = 0
        self._frame_duration_ns = Gst.SECOND // 30

        self._lock = threading.Lock()
        self._bus_timer = QTimer(self)
        self._bus_timer.setInterval(150)
        self._bus_timer.timeout.connect(self._poll_buses)

    @property
    def is_previewing(self) -> bool:
        """Return whether the preview pipeline is currently running."""
        return self._preview_pipeline is not None

    @property
    def is_recording(self) -> bool:
        """Return whether the recording pipeline is currently running."""
        return self._record_pipeline is not None

    def start_preview(
        self,
        source_mode: str,
        address: str,
        port: int,
        local_camera_index: int,
    ) -> None:
        """Start a preview pipeline for the selected source mode."""
        self.stop_preview()
        pipeline_desc = self._build_preview_pipeline(
            source_mode=source_mode,
            address=address,
            port=port,
            local_camera_index=local_camera_index,
        )
        try:
            pipeline = Gst.parse_launch(pipeline_desc)
        except Exception as exc:
            self.preview_error.emit(f"Failed to create preview pipeline.\n{exc}")
            return

        sink = pipeline.get_by_name("preview_sink")
        if sink is None:
            self.preview_error.emit("Preview pipeline started without an appsink named preview_sink.")
            pipeline.set_state(Gst.State.NULL)
            return

        sink.connect("new-sample", self._on_new_sample)

        self._preview_pipeline = pipeline
        self._preview_bus = pipeline.get_bus()
        self._preview_sink = sink

        state_change = pipeline.set_state(Gst.State.PLAYING)
        if state_change == Gst.StateChangeReturn.FAILURE:
            self.preview_error.emit("Preview pipeline failed to start.")
            self.stop_preview()
            return

        self._bus_timer.start()
        self.preview_state_changed.emit("Preview started.")

    def stop_preview(self) -> None:
        """Stop preview and recording pipelines safely."""
        had_preview = self._preview_pipeline is not None
        self.stop_recording()
        if self._preview_pipeline is not None:
            self._preview_pipeline.set_state(Gst.State.NULL)
        self._preview_pipeline = None
        self._preview_bus = None
        self._preview_sink = None
        self._bus_timer.stop()
        if had_preview:
            self.preview_state_changed.emit("Preview stopped.")

    def start_recording(self, output_path: str | Path) -> tuple[bool, str]:
        """Start the local MP4 recording pipeline fed by preview frames."""
        with self._lock:
            if self._record_pipeline is not None:
                return False, "Recording is already running."
            if self._frame_width <= 0 or self._frame_height <= 0 or not self._latest_frame_bytes:
                return False, "Recording requires an active preview frame first."

            try:
                pipeline_desc = self._build_record_pipeline(Path(output_path))
                pipeline = Gst.parse_launch(pipeline_desc)
            except Exception as exc:
                return False, f"Failed to create recording pipeline.\n{exc}"

            appsrc = pipeline.get_by_name("record_src")
            if appsrc is None:
                pipeline.set_state(Gst.State.NULL)
                return False, "Recording pipeline started without an appsrc named record_src."

            state_change = pipeline.set_state(Gst.State.PLAYING)
            if state_change == Gst.StateChangeReturn.FAILURE:
                pipeline.set_state(Gst.State.NULL)
                return False, "Recording pipeline failed to start."

            self._record_pipeline = pipeline
            self._record_bus = pipeline.get_bus()
            self._record_appsrc = appsrc
            self._frame_index = 0
            self._bus_timer.start()

        self.recording_state_changed.emit(f"Recording to {output_path}")
        return True, ""

    def stop_recording(self) -> None:
        """Flush and stop the active recording pipeline."""
        had_recording = self._record_pipeline is not None
        if not had_recording:
            return

        status_message = "Recording stopped."
        with self._lock:
            if self._record_appsrc is not None:
                try:
                    # Push EOS first so mp4mux can finish writing the file footer.
                    self._record_appsrc.emit("end-of-stream")
                except Exception:
                    pass
            message = None
            if self._record_bus is not None:
                message = self._record_bus.timed_pop_filtered(
                    2 * Gst.SECOND,
                    Gst.MessageType.EOS | Gst.MessageType.ERROR,
                )
            if message is not None and message.type == Gst.MessageType.ERROR:
                err, debug_info = message.parse_error()
                status_message = f"GStreamer error while stopping recording: {err.message}"
                if debug_info:
                    status_message += f"\n{debug_info}"

        self._finalize_recording_pipeline(status_message)

    def capture_frame(self, output_path: str | Path) -> tuple[bool, str]:
        """Save the latest preview frame as a JPG image."""
        with self._lock:
            if self._latest_image.isNull():
                return False, "No preview frame is available yet."
            image = self._latest_image.copy()

        saved = image.save(str(output_path), "JPG", 95)
        if not saved:
            return False, f"Failed to save JPG file: {output_path}"
        return True, ""

    def _build_preview_pipeline(
        self,
        source_mode: str,
        address: str,
        port: int,
        local_camera_index: int,
    ) -> str:
        """Create the GStreamer pipeline description for the selected preview source."""
        if source_mode == "local":
            return self._build_local_camera_pipeline(local_camera_index)
        return self._build_network_preview_pipeline(address=address, port=port)

    def _build_network_preview_pipeline(self, address: str, port: int) -> str:
        """Create the network RTP/H264 preview pipeline."""
        safe_address = address.strip() or "0.0.0.0"
        return (
            f'udpsrc address={safe_address} port={int(port)} '
            'caps="application/x-rtp, media=video, encoding-name=H264, payload=96, clock-rate=90000" ! '
            "rtpjitterbuffer latency=50 drop-on-latency=true ! "
            "rtph264depay ! "
            "h264parse ! "
            "decodebin ! "
            "videoconvert ! "
            "video/x-raw,format=RGB ! "
            "appsink name=preview_sink emit-signals=true sync=false max-buffers=1 drop=true"
        )

    def _build_local_camera_pipeline(self, local_camera_index: int) -> str:
        """Create a local camera preview pipeline for Windows testing."""
        source_desc = self._select_local_camera_source(local_camera_index)
        return (
            f"{source_desc} ! "
            "queue ! "
            "videoconvert ! "
            "video/x-raw,format=RGB ! "
            "appsink name=preview_sink emit-signals=true sync=false max-buffers=1 drop=true"
        )

    def _select_local_camera_source(self, local_camera_index: int) -> str:
        """Pick the first local camera source plugin available on this Windows machine."""
        safe_index = max(0, int(local_camera_index))
        for factory_name in ("ksvideosrc", "mfvideosrc"):
            if Gst.ElementFactory.find(factory_name) is not None:
                # device-index lets us test built-in or USB cameras without changing code.
                return f"{factory_name} device-index={safe_index}"
        if Gst.ElementFactory.find("autovideosrc") is not None:
            return "autovideosrc"
        raise RuntimeError(
            "No local camera source plugin was found. Install ksvideosrc or mfvideosrc support in GStreamer."
        )

    def _build_record_pipeline(self, output_path: Path) -> str:
        """Create the MP4 recording pipeline fed from appsrc."""
        encoder_desc = self._select_encoder_pipeline()
        safe_path = str(output_path).replace("\\", "\\\\")
        return (
            "appsrc name=record_src is-live=true block=true format=time "
            f'caps="video/x-raw,format=RGB,width={self._frame_width},height={self._frame_height},framerate=30/1" ! '
            "queue ! "
            "videoconvert ! "
            # Force 4:2:0 pixel format before H264 encoding so Windows players can decode the MP4 reliably.
            "video/x-raw,format=I420 ! "
            f"{encoder_desc} ! "
            "h264parse ! "
            "mp4mux faststart=true ! "
            f'filesink location="{safe_path}"'
        )

    def _select_encoder_pipeline(self) -> str:
        """Pick the first available H264 encoder from the installed GStreamer plugins."""
        if Gst.ElementFactory.find("x264enc") is not None:
            # The downstream caps pin the encoder to a widely supported 8-bit 4:2:0 profile.
            return (
                "x264enc tune=zerolatency speed-preset=ultrafast bitrate=4000 key-int-max=30 "
                "! video/x-h264,profile=main"
            )
        if Gst.ElementFactory.find("openh264enc") is not None:
            return "openh264enc bitrate=4000000 complexity=low ! video/x-h264,profile=main"
        raise RuntimeError("No H264 encoder plugin was found. Install x264enc or openh264enc.")

    def _on_new_sample(self, sink: Gst.Element) -> Gst.FlowReturn:
        """Convert the latest appsink sample into a Qt image and forward it to the UI."""
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR

        buffer = sample.get_buffer()
        caps = sample.get_caps()
        if buffer is None or caps is None:
            return Gst.FlowReturn.ERROR

        structure = caps.get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")

        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR

        try:
            frame_bytes = bytes(map_info.data)
            image = QImage(frame_bytes, width, height, width * 3, QImage.Format.Format_RGB888).copy()
        finally:
            buffer.unmap(map_info)

        with self._lock:
            self._latest_image = image
            self._latest_frame_bytes = frame_bytes
            self._frame_width = width
            self._frame_height = height

        # Emit the frame to Qt first, then mirror it into the recorder if recording is active.
        self.frame_ready.emit(image)
        self._push_frame_to_recorder(frame_bytes)
        return Gst.FlowReturn.OK

    def _push_frame_to_recorder(self, frame_bytes: bytes) -> None:
        """Forward preview frames into appsrc while a recording session is active."""
        with self._lock:
            if self._record_appsrc is None:
                return

            gst_buffer = Gst.Buffer.new_allocate(None, len(frame_bytes), None)
            gst_buffer.fill(0, frame_bytes)
            # Monotonic PTS values keep MP4 playback speed stable after remuxing.
            gst_buffer.pts = self._frame_index * self._frame_duration_ns
            gst_buffer.dts = gst_buffer.pts
            gst_buffer.duration = self._frame_duration_ns
            self._frame_index += 1
            self._record_appsrc.emit("push-buffer", gst_buffer)

    def _poll_buses(self) -> None:
        """Read preview and recording pipeline messages without requiring a GLib main loop."""
        self._drain_bus(self._preview_bus, preview_bus=True)
        self._drain_bus(self._record_bus, preview_bus=False)

    def _finalize_recording_pipeline(self, status_message: str) -> None:
        """Release the recorder pipeline and publish a single final status update."""
        with self._lock:
            if self._record_pipeline is not None:
                self._record_pipeline.set_state(Gst.State.NULL)
            self._record_pipeline = None
            self._record_bus = None
            self._record_appsrc = None
            self._frame_index = 0

        self.recording_state_changed.emit(status_message)

    def _drain_bus(self, bus: Gst.Bus | None, *, preview_bus: bool) -> None:
        """Process pending GstBus messages for a single pipeline."""
        if bus is None:
            return

        while True:
            message = bus.timed_pop_filtered(
                0,
                Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING,
            )
            if message is None:
                break

            if message.type == Gst.MessageType.WARNING:
                err, debug_info = message.parse_warning()
                text = f"GStreamer warning: {err.message}"
                if debug_info:
                    text += f"\n{debug_info}"
                if preview_bus:
                    self.preview_state_changed.emit(text)
                else:
                    self.recording_state_changed.emit(text)
                continue

            if message.type == Gst.MessageType.ERROR:
                err, debug_info = message.parse_error()
                text = f"GStreamer error: {err.message}"
                if debug_info:
                    text += f"\n{debug_info}"
                if preview_bus:
                    self.preview_error.emit(text)
                    self.stop_preview()
                else:
                    self._finalize_recording_pipeline(text)
                continue

            if message.type == Gst.MessageType.EOS:
                if preview_bus:
                    self.preview_state_changed.emit("Preview stream ended.")
                    self.stop_preview()
                else:
                    self._finalize_recording_pipeline("Recording saved.")
