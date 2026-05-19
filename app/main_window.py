"""Main window for the integrated single-video camera + claw joystick application."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSignalBlocker, QTimer, Qt, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStackedLayout,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .joystick_backend import BRIDGE_PYTHON, PYGAME_AVAILABLE, JoystickSnapshot, JoystickWorker
from .settings import AppSettings, AppSettingsStore
from .storage import StorageManager
from .video_stream import VideoStreamClient


def _apply_compact_spacing(layout) -> None:
    """Use compact margins and spacing without collapsing controls together."""
    layout.setContentsMargins(6, 6, 6, 6)
    layout.setSpacing(4)


def _readonly_field(text: str = "") -> QLineEdit:
    """Create one stable read-only field for runtime values."""
    field = QLineEdit(text)
    field.setReadOnly(True)
    field.setMinimumHeight(26)
    field.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return field


def _group_box(title: str) -> QGroupBox:
    """Create one standard group box for the fixed dashboard layout."""
    group = QGroupBox(title)
    group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return group


def _inline_form_row(*items: tuple[str, QWidget]) -> QWidget:
    """Create one compact horizontal row that packs multiple label+field pairs."""
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    for label_text, field in items:
        label = QLabel(label_text)
        label.setMinimumWidth(48)
        layout.addWidget(label)
        layout.addWidget(field, 1)
    return container


class IntegratedMainWindow(QMainWindow):
    """Desktop UI that unifies one video view, capture controls, and claw joystick telemetry."""

    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self._project_root = project_root
        self._settings_store = AppSettingsStore()
        self._config = self._settings_store.load()
        self._storage = StorageManager()
        self._video_client = VideoStreamClient(self)
        self._joystick_worker: JoystickWorker | None = None

        self._last_frame = QImage()
        self._preview_requested = False
        self._auto_capture_running = False
        self._last_joystick_snapshot = JoystickSnapshot(
            endpoint=f"{self._config.joystick_host}:{self._config.joystick_port}"
        )

        self._auto_capture_timer = QTimer(self)
        self._auto_capture_timer.timeout.connect(self._capture_single_frame)

        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.timeout.connect(self._retry_preview)

        self._build_ui()
        self._connect_signals()
        self._load_settings_to_ui()
        self._update_runtime_state()
        self._update_video_badges()
        self._set_status_field(self.link_joystick_status_field, "Disconnected", "#d13438")
        self._set_status_field(self.link_rov_status_field, "Disconnected", "#d13438")

        if PYGAME_AVAILABLE:
            self._start_joystick_backend()
        else:
            self.joystick_runtime_field.setText("Bridge missing")
            self._set_status_field(self.link_joystick_status_field, "Unavailable", "#b58900")
            self.last_error_field.setText("FinsRov python.exe not found")
            self.statusBar().showMessage(
                "Joystick bridge is unavailable because FinsRov python.exe was not found.",
                6000,
            )

    def _build_ui(self) -> None:
        """Create the full integrated layout."""
        self.setWindowTitle("ROV Integrated Console")
        self.resize(1760, 940)
        self.setMinimumSize(1440, 820)

        central = QWidget(self)
        central_layout = QHBoxLayout(central)
        _apply_compact_spacing(central_layout)
        self.setCentralWidget(central)

        left_panel = QWidget(self)
        left_layout = QVBoxLayout(left_panel)
        _apply_compact_spacing(left_layout)

        video_group = _group_box("Video")
        video_group_layout = QVBoxLayout(video_group)
        _apply_compact_spacing(video_group_layout)

        video_frame = QFrame(self)
        video_frame.setFrameShape(QFrame.Shape.StyledPanel)
        video_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        video_stack = QStackedLayout(video_frame)
        video_stack.setStackingMode(QStackedLayout.StackingMode.StackAll)

        self.main_video_label = QLabel("No video frame")
        self.main_video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_video_label.setMinimumSize(760, 560)
        self.main_video_label.setStyleSheet(
            "background-color: #101418; color: #d7dde5; border: 1px solid #40464d; font-size: 18px;"
        )
        video_stack.addWidget(self.main_video_label)

        overlay_widget = QWidget(video_frame)
        overlay_layout = QVBoxLayout(overlay_widget)
        overlay_layout.setContentsMargins(0, 10, 10, 0)
        overlay_layout.setSpacing(6)
        overlay_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)

        self.recording_badge = QLabel("REC")
        self.recording_badge.setStyleSheet(
            "background:#d13438;color:white;font-weight:700;padding:4px 10px;border-radius:12px;"
        )
        self.auto_capture_badge = QLabel("AUTO CAPTURE")
        self.auto_capture_badge.setStyleSheet(
            "background:#ffb900;color:#111;font-weight:700;padding:4px 10px;border-radius:12px;"
        )
        overlay_layout.addWidget(self.recording_badge)
        overlay_layout.addWidget(self.auto_capture_badge)
        overlay_layout.addStretch(1)
        video_stack.addWidget(overlay_widget)

        video_group_layout.addWidget(video_frame)
        left_layout.addWidget(video_group, stretch=1)

        right_panel = QWidget(self)
        right_panel.setMinimumWidth(560)
        right_panel.setMaximumWidth(680)
        right_panel.setStyleSheet(
            """
            QGroupBox {
                font-size: 11px;
                margin-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 2px;
            }
            QLabel {
                font-size: 11px;
            }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPushButton, QPlainTextEdit {
                font-size: 11px;
            }
            """
        )
        right_layout = QGridLayout(right_panel)
        _apply_compact_spacing(right_layout)
        right_layout.setColumnStretch(0, 1)
        right_layout.setColumnStretch(1, 1)
        right_layout.setRowStretch(0, 0)
        right_layout.setRowStretch(1, 0)
        right_layout.setRowStretch(2, 0)
        right_layout.setRowStretch(3, 1)

        stream_group = _group_box("Video Stream")
        stream_form = QFormLayout(stream_group)
        _apply_compact_spacing(stream_form)
        stream_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.source_mode_combo = QComboBox()
        self.source_mode_combo.addItem("Network RTP", "network")
        self.source_mode_combo.addItem("Local Camera", "local")
        self.address_edit = QLineEdit()
        self.address_edit.setPlaceholderText("0.0.0.0")
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1, 65535)
        self.local_camera_index_spin = QSpinBox()
        self.local_camera_index_spin.setRange(0, 15)
        stream_form.addRow(
            _inline_form_row(("Source", self.source_mode_combo), ("Camera", self.local_camera_index_spin))
        )
        stream_form.addRow(_inline_form_row(("Address", self.address_edit), ("Port", self.port_spin)))

        capture_storage_group = _group_box("Capture & Storage")
        capture_storage_layout = QVBoxLayout(capture_storage_group)
        _apply_compact_spacing(capture_storage_layout)
        capture_storage_form = QFormLayout()
        _apply_compact_spacing(capture_storage_form)
        capture_storage_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.2, 3600.0)
        self.interval_spin.setSingleStep(0.5)
        self.interval_spin.setSuffix(" s")
        self.save_dir_edit = QLineEdit()
        self.save_dir_edit.setReadOnly(True)
        self.save_dir_edit.setPlaceholderText("Capture root directory")
        capture_storage_form.addRow("Auto interval", self.interval_spin)
        capture_storage_form.addRow("Save root", self.save_dir_edit)
        capture_storage_layout.addLayout(capture_storage_form)

        capture_storage_buttons = QGridLayout()
        _apply_compact_spacing(capture_storage_buttons)
        self.change_dir_button = QPushButton("Change dir")
        self.open_dir_button = QPushButton("Open dir")
        self.single_capture_button = QPushButton("Take photo")
        self.start_auto_button = QPushButton("Start auto")
        self.stop_auto_button = QPushButton("Stop auto")
        capture_storage_buttons.addWidget(self.change_dir_button, 0, 0)
        capture_storage_buttons.addWidget(self.open_dir_button, 0, 1)
        capture_storage_buttons.addWidget(self.single_capture_button, 1, 0)
        capture_storage_buttons.addWidget(self.start_auto_button, 1, 1)
        capture_storage_buttons.addWidget(self.stop_auto_button, 1, 2)
        capture_storage_layout.addLayout(capture_storage_buttons)

        preview_record_group = _group_box("Preview · Runtime")
        preview_record_layout = QGridLayout(preview_record_group)
        _apply_compact_spacing(preview_record_layout)
        self.start_preview_button = QPushButton("Start preview")
        self.stop_preview_button = QPushButton("Stop preview")
        self.start_record_button = QPushButton("Start recording")
        self.stop_record_button = QPushButton("Stop recording")
        preview_record_layout.addWidget(self.start_preview_button, 0, 0)
        preview_record_layout.addWidget(self.stop_preview_button, 0, 1)
        preview_record_layout.addWidget(self.start_record_button, 1, 0)
        preview_record_layout.addWidget(self.stop_record_button, 1, 1)
        self.preview_state_field = _readonly_field("Stopped")
        self.capture_state_field = _readonly_field("Idle")
        self.record_state_field = _readonly_field("Idle")
        bridge_name = BRIDGE_PYTHON.name if BRIDGE_PYTHON is not None else "Missing"
        self.joystick_runtime_field = _readonly_field(bridge_name)
        preview_record_layout.addWidget(
            _inline_form_row(("Preview", self.preview_state_field), ("Recording", self.record_state_field)),
            2,
            0,
            1,
            2,
        )
        preview_record_layout.addWidget(
            _inline_form_row(("Auto", self.capture_state_field), ("Joystick", self.joystick_runtime_field)),
            3,
            0,
            1,
            2,
        )

        joystick_link_group = _group_box("Joystick Link")
        joystick_link_layout = QVBoxLayout(joystick_link_group)
        _apply_compact_spacing(joystick_link_layout)
        joystick_link_grid = QGridLayout()
        _apply_compact_spacing(joystick_link_grid)
        self.link_joystick_status_field = _readonly_field("Disconnected")
        self.link_rov_status_field = _readonly_field("Disconnected")
        joystick_link_grid.addWidget(QLabel("Joystick"), 0, 0)
        joystick_link_grid.addWidget(self.link_joystick_status_field, 0, 1)
        joystick_link_grid.addWidget(QLabel("ROV"), 1, 0)
        joystick_link_grid.addWidget(self.link_rov_status_field, 1, 1)
        joystick_link_grid.setColumnStretch(1, 1)
        joystick_link_layout.addLayout(joystick_link_grid)

        joystick_link_buttons = QHBoxLayout()
        _apply_compact_spacing(joystick_link_buttons)
        self.start_joystick_button = QPushButton("Start joystick")
        self.stop_joystick_button = QPushButton("Stop joystick")
        joystick_link_buttons.addWidget(self.start_joystick_button)
        joystick_link_buttons.addWidget(self.stop_joystick_button)
        joystick_link_layout.addLayout(joystick_link_buttons)

        joystick_state_group = _group_box("Joystick State")
        joystick_state_form = QFormLayout(joystick_state_group)
        _apply_compact_spacing(joystick_state_form)
        joystick_state_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.hover_field = _readonly_field("Off")
        self.yaw_loop_field = _readonly_field("Off")
        self.left_axes_field = _readonly_field("0.00, 0.00")
        self.right_axes_field = _readonly_field("0.00, 0.00")
        self.planar_field = _readonly_field("x=0, y=0")
        self.yaw_depth_field = _readonly_field("yaw +0 deg/s, depth +0")
        self.pressed_buttons_field = _readonly_field("[]")
        joystick_state_form.addRow(
            _inline_form_row(("Hover", self.hover_field), ("Yaw loop", self.yaw_loop_field))
        )
        joystick_state_form.addRow("Left axes", self.left_axes_field)
        joystick_state_form.addRow("Right axes", self.right_axes_field)
        joystick_state_form.addRow("Planar", self.planar_field)
        joystick_state_form.addRow("Yaw / Depth", self.yaw_depth_field)
        joystick_state_form.addRow("Pressed", self.pressed_buttons_field)

        claw_gimbal_group = _group_box("Claw & Gimbal")
        claw_gimbal_form = QFormLayout(claw_gimbal_group)
        _apply_compact_spacing(claw_gimbal_form)
        claw_gimbal_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.depth_target_field = _readonly_field("300")
        self.gripper_pwm_field = _readonly_field("--")
        self.gimbal_tilt_field = _readonly_field("1200")
        self.gimbal_pan_field = _readonly_field("1550")
        self.last_error_field = _readonly_field("")
        claw_gimbal_form.addRow("Depth", self.depth_target_field)
        claw_gimbal_form.addRow("Gripper PWM", self.gripper_pwm_field)
        claw_gimbal_form.addRow(
            _inline_form_row(("Tilt PWM", self.gimbal_tilt_field), ("Pan PWM", self.gimbal_pan_field))
        )
        claw_gimbal_form.addRow("Last error", self.last_error_field)

        logs_group = _group_box("Joystick Logs")
        logs_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        logs_layout = QGridLayout(logs_group)
        _apply_compact_spacing(logs_layout)
        logs_layout.addWidget(QLabel("Sent"), 0, 0)
        logs_layout.addWidget(QLabel("Received"), 0, 1)
        self.send_log_edit = QPlainTextEdit()
        self.send_log_edit.setReadOnly(True)
        self.send_log_edit.setMinimumHeight(110)
        self.recv_log_edit = QPlainTextEdit()
        self.recv_log_edit.setReadOnly(True)
        self.recv_log_edit.setMinimumHeight(110)
        logs_layout.addWidget(self.send_log_edit, 1, 0)
        logs_layout.addWidget(self.recv_log_edit, 1, 1)

        right_layout.addWidget(stream_group, 0, 0)
        right_layout.addWidget(joystick_link_group, 0, 1)
        right_layout.addWidget(capture_storage_group, 1, 0)
        right_layout.addWidget(joystick_state_group, 1, 1)
        right_layout.addWidget(preview_record_group, 2, 0)
        right_layout.addWidget(claw_gimbal_group, 2, 1)
        right_layout.addWidget(logs_group, 3, 0, 1, 2)

        self._apply_compact_control_sizing()

        central_layout.addWidget(left_panel, stretch=15)
        central_layout.addWidget(right_panel, stretch=7)

        status_bar = QStatusBar(self)
        self.setStatusBar(status_bar)
        self.statusBar().showMessage("Ready.")

        open_project_action = QAction("Open app folder", self)
        open_project_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._project_root)))
        )
        self.menuBar().addAction(open_project_action)

    def _apply_compact_control_sizing(self) -> None:
        """Set consistent sizes so the fixed layout stays readable on one screen."""
        inputs = [
            self.source_mode_combo,
            self.address_edit,
            self.port_spin,
            self.local_camera_index_spin,
            self.interval_spin,
            self.save_dir_edit,
        ]
        buttons = [
            self.change_dir_button,
            self.open_dir_button,
            self.single_capture_button,
            self.start_auto_button,
            self.stop_auto_button,
            self.start_preview_button,
            self.stop_preview_button,
            self.start_record_button,
            self.stop_record_button,
            self.start_joystick_button,
            self.stop_joystick_button,
        ]
        displays = [
            self.preview_state_field,
            self.capture_state_field,
            self.record_state_field,
            self.joystick_runtime_field,
            self.link_joystick_status_field,
            self.link_rov_status_field,
            self.hover_field,
            self.yaw_loop_field,
            self.left_axes_field,
            self.right_axes_field,
            self.planar_field,
            self.yaw_depth_field,
            self.pressed_buttons_field,
            self.depth_target_field,
            self.gripper_pwm_field,
            self.gimbal_tilt_field,
            self.gimbal_pan_field,
            self.last_error_field,
        ]
        for control in inputs:
            control.setMinimumHeight(26)
        for control in buttons:
            control.setMinimumHeight(28)
        for control in displays:
            control.setMinimumHeight(26)

    def _set_status_field(self, field: QLineEdit, text: str, color: str) -> None:
        """Render one connection status field with explicit colors."""
        field.setText(text)
        field.setStyleSheet(f"color: {color}; font-weight: 700; background: #ffffff;")

    def _connect_signals(self) -> None:
        """Wire UI events and backend events to handlers."""
        self.start_preview_button.clicked.connect(self._start_preview)
        self.stop_preview_button.clicked.connect(self._stop_preview)
        self.single_capture_button.clicked.connect(self._capture_single_frame)
        self.start_auto_button.clicked.connect(self._start_auto_capture)
        self.stop_auto_button.clicked.connect(self._stop_auto_capture)
        self.start_record_button.clicked.connect(self._start_recording)
        self.stop_record_button.clicked.connect(self._stop_recording)
        self.change_dir_button.clicked.connect(self._choose_save_directory)
        self.open_dir_button.clicked.connect(self._open_save_directory)
        self.start_joystick_button.clicked.connect(self._start_joystick_backend)
        self.stop_joystick_button.clicked.connect(self._stop_joystick_backend)

        self.address_edit.editingFinished.connect(self._save_ui_settings)
        self.port_spin.valueChanged.connect(self._save_ui_settings)
        self.local_camera_index_spin.valueChanged.connect(self._save_ui_settings)
        self.source_mode_combo.currentIndexChanged.connect(self._on_source_mode_changed)
        self.interval_spin.valueChanged.connect(self._on_interval_changed)

        self._video_client.frame_ready.connect(self._update_preview_frame)
        self._video_client.preview_state_changed.connect(self._on_preview_state_changed)
        self._video_client.preview_error.connect(self._on_preview_error)
        self._video_client.recording_state_changed.connect(self._on_recording_state_changed)

    def _load_settings_to_ui(self) -> None:
        """Populate all fields from persisted settings."""
        combo_index = max(0, self.source_mode_combo.findData(self._config.source_mode))
        with QSignalBlocker(self.source_mode_combo):
            self.source_mode_combo.setCurrentIndex(combo_index)
        with QSignalBlocker(self.address_edit):
            self.address_edit.setText(self._config.stream_address)
        with QSignalBlocker(self.port_spin):
            self.port_spin.setValue(self._config.stream_port)
        with QSignalBlocker(self.local_camera_index_spin):
            self.local_camera_index_spin.setValue(self._config.local_camera_index)
        with QSignalBlocker(self.interval_spin):
            self.interval_spin.setValue(self._config.capture_interval_s)

        self.save_dir_edit.setText(self._config.save_root)
        self._apply_capture_interval()
        self._update_source_mode_ui()
        self._render_current_frame()

    def _save_ui_settings(self) -> None:
        """Persist the latest editable values from the UI."""
        self._config = AppSettings(
            source_mode=self.source_mode_combo.currentData(),
            stream_address=self.address_edit.text().strip() or "0.0.0.0",
            stream_port=self.port_spin.value(),
            local_camera_index=self.local_camera_index_spin.value(),
            joystick_host=self._config.joystick_host,
            joystick_port=self._config.joystick_port,
            capture_interval_s=self.interval_spin.value(),
            save_root=self.save_dir_edit.text().strip(),
            reconnect_interval_ms=self._config.reconnect_interval_ms,
        )
        self._settings_store.save(self._config)

    def _on_source_mode_changed(self) -> None:
        """Update source-specific controls when the preview source changes."""
        self._update_source_mode_ui()
        self._save_ui_settings()

    def _update_source_mode_ui(self) -> None:
        """Enable only the source controls relevant to the active mode."""
        is_network = self.source_mode_combo.currentData() == "network"
        self.address_edit.setEnabled(is_network)
        self.port_spin.setEnabled(is_network)
        self.local_camera_index_spin.setEnabled(not is_network)

    def _on_interval_changed(self) -> None:
        """Update the auto-capture timer immediately when the interval changes."""
        self._apply_capture_interval()
        self._save_ui_settings()
        self.statusBar().showMessage("Auto capture interval updated.", 3000)

    def _apply_capture_interval(self) -> None:
        """Translate the spin-box value into the timer interval used internally."""
        self._auto_capture_timer.setInterval(int(self.interval_spin.value() * 1000))

    def _start_preview(self) -> None:
        """Start preview and remember that reconnect attempts are allowed."""
        self._save_ui_settings()
        self._preview_requested = True
        self._reconnect_timer.stop()
        self._video_client.start_preview(
            source_mode=self.source_mode_combo.currentData(),
            address=self.address_edit.text().strip() or "0.0.0.0",
            port=self.port_spin.value(),
            local_camera_index=self.local_camera_index_spin.value(),
        )
        self._update_runtime_state()

    def _retry_preview(self) -> None:
        """Retry preview after a transport error if the user still wants it running."""
        if not self._preview_requested:
            return
        self.statusBar().showMessage("Retrying preview...", 3000)
        self._video_client.start_preview(
            source_mode=self.source_mode_combo.currentData(),
            address=self.address_edit.text().strip() or "0.0.0.0",
            port=self.port_spin.value(),
            local_camera_index=self.local_camera_index_spin.value(),
        )
        self._update_runtime_state()

    def _stop_preview(self) -> None:
        """Stop preview and cancel reconnect attempts."""
        self._preview_requested = False
        self._reconnect_timer.stop()
        self._video_client.stop_preview()
        self._render_current_frame()
        self._update_runtime_state()

    def _start_joystick_backend(self) -> None:
        """Start the joystick bridge process using the current host and port settings."""
        self._save_ui_settings()
        self._stop_joystick_backend()

        worker = JoystickWorker(self._config.joystick_host, self._config.joystick_port, self)
        worker.snapshot_ready.connect(self._on_joystick_snapshot)
        worker.status_message.connect(self._on_joystick_status_message)
        worker.finished.connect(self._update_runtime_state)
        self._joystick_worker = worker
        worker.start()
        self._update_runtime_state()

    def _stop_joystick_backend(self) -> None:
        """Stop the joystick bridge process if it is running."""
        if self._joystick_worker is None:
            return

        worker = self._joystick_worker
        self._joystick_worker = None
        try:
            worker.snapshot_ready.disconnect(self._on_joystick_snapshot)
            worker.status_message.disconnect(self._on_joystick_status_message)
            worker.finished.disconnect(self._update_runtime_state)
        except (RuntimeError, TypeError):
            pass
        worker.stop()
        worker.deleteLater()
        self.joystick_runtime_field.setText(BRIDGE_PYTHON.name if BRIDGE_PYTHON is not None else "Stopped")
        self._update_runtime_state()

    def _update_preview_frame(self, image: QImage) -> None:
        """Store and render the latest preview frame."""
        self._last_frame = image
        self._render_current_frame()

    def _render_current_frame(self) -> None:
        """Scale the latest frame into the single video view."""
        if self._last_frame.isNull():
            self.main_video_label.setText("No video frame")
            self.main_video_label.setPixmap(QPixmap())
            self._update_video_badges()
            return

        pixmap = QPixmap.fromImage(self._last_frame)
        scaled = pixmap.scaled(
            self.main_video_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.main_video_label.setPixmap(scaled)
        self.main_video_label.setText("")
        self._update_video_badges()

    def _update_video_badges(self) -> None:
        """Refresh the recording and auto-capture badges in the main video area."""
        self.recording_badge.setVisible(self._video_client.is_recording)
        self.auto_capture_badge.setVisible(self._auto_capture_running)

    def _start_auto_capture(self) -> None:
        """Enable periodic snapshots using the configured timer interval."""
        if not self._ensure_storage_available():
            return
        if self._last_frame.isNull():
            QMessageBox.warning(self, "No Frame", "Start preview first so the app has a frame to capture.")
            return

        self._auto_capture_running = True
        self._apply_capture_interval()
        self._auto_capture_timer.start()
        self.capture_state_field.setText("Running")
        self._update_video_badges()
        self.statusBar().showMessage("Auto capture started.", 3000)
        self._update_runtime_state()

    def _stop_auto_capture(self) -> None:
        """Disable periodic snapshots safely."""
        self._auto_capture_timer.stop()
        self._auto_capture_running = False
        self.capture_state_field.setText("Idle")
        self._update_video_badges()
        self.statusBar().showMessage("Auto capture stopped.", 3000)
        self._update_runtime_state()

    def _capture_single_frame(self) -> None:
        """Save one JPG file using the most recent preview frame."""
        if not self._ensure_storage_available():
            return

        output_path = self._storage.build_photo_path(self.save_dir_edit.text())
        ok, message = self._video_client.capture_frame(output_path)
        if not ok:
            self.statusBar().showMessage(message, 6000)
            if not self._auto_capture_running:
                QMessageBox.warning(self, "Capture Failed", message)
            return

        self.statusBar().showMessage(f"Saved photo: {output_path}", 5000)

    def _start_recording(self) -> None:
        """Start MP4 recording using the live preview frames."""
        if not self._ensure_storage_available():
            return

        output_path = self._storage.build_video_path(self.save_dir_edit.text())
        ok, message = self._video_client.start_recording(output_path)
        if not ok:
            QMessageBox.warning(self, "Recording Failed", message)
            self.statusBar().showMessage(message, 6000)
            return

        self.record_state_field.setText("Recording")
        self._update_video_badges()
        self.statusBar().showMessage(f"Recording started: {output_path}", 5000)
        self._update_runtime_state()

    def _stop_recording(self) -> None:
        """Stop MP4 recording if a session is active."""
        if self._video_client.is_recording:
            self._video_client.stop_recording()
        self.record_state_field.setText("Idle")
        self._update_video_badges()
        self._update_runtime_state()

    def _choose_save_directory(self) -> None:
        """Open a directory chooser and update the capture root."""
        current_root = self.save_dir_edit.text().strip() or str(Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Select save directory", current_root)
        if not selected:
            return

        self.save_dir_edit.setText(selected)
        self._save_ui_settings()
        self.statusBar().showMessage(f"Save directory updated: {selected}", 4000)

    def _open_save_directory(self) -> None:
        """Open the selected save root in Windows Explorer."""
        root = self.save_dir_edit.text().strip()
        if not root:
            QMessageBox.warning(self, "No Directory", "Please choose a save directory first.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(root))

    def _ensure_storage_available(self) -> bool:
        """Validate the save root before starting any file output operation."""
        self._save_ui_settings()
        ok, message = self._storage.ensure_root_writable(self.save_dir_edit.text())
        if not ok:
            QMessageBox.critical(self, "Directory Error", message)
            self.statusBar().showMessage(message, 6000)
            return False
        return True

    def _on_preview_state_changed(self, message: str) -> None:
        """Reflect preview lifecycle messages in the UI."""
        self.preview_state_field.setText("Running" if self._video_client.is_previewing else "Stopped")
        self.statusBar().showMessage(message, 4000)
        self._update_runtime_state()

    def _on_preview_error(self, message: str) -> None:
        """Show preview transport or pipeline errors and schedule a reconnect."""
        self.preview_state_field.setText("Error")
        self.statusBar().showMessage(message, 6000)
        if self._preview_requested:
            self._reconnect_timer.start(self._config.reconnect_interval_ms)
        self._update_runtime_state()

    def _on_recording_state_changed(self, message: str) -> None:
        """Refresh the recording label after start, stop, or pipeline messages."""
        self.record_state_field.setText("Recording" if self._video_client.is_recording else "Idle")
        self._update_video_badges()
        self.statusBar().showMessage(message, 5000)
        self._update_runtime_state()

    def _on_joystick_snapshot(self, snapshot: JoystickSnapshot) -> None:
        """Refresh the joystick state panels from the latest backend snapshot."""
        self._last_joystick_snapshot = snapshot
        self.joystick_runtime_field.setText("Running")
        self._set_status_field(
            self.link_joystick_status_field,
            "Connected" if snapshot.joystick_connected else "Disconnected",
            "#107c10" if snapshot.joystick_connected else "#d13438",
        )
        self._set_status_field(
            self.link_rov_status_field,
            "Connected" if snapshot.tcp_connected else "Disconnected",
            "#107c10" if snapshot.tcp_connected else "#d13438",
        )
        self.hover_field.setText("On" if snapshot.hover_enabled else "Off")
        self.yaw_loop_field.setText("On" if snapshot.yaw_loop_enabled else "Off")
        self.left_axes_field.setText(f"{snapshot.left_h:+.2f}, {snapshot.left_v:+.2f}")
        self.right_axes_field.setText(f"{snapshot.right_h:+.2f}, {snapshot.right_v:+.2f}")
        self.planar_field.setText(f"x={snapshot.planar_x}, y={snapshot.planar_y}")
        self.yaw_depth_field.setText(f"yaw {snapshot.yaw_rate_cmd:+d} deg/s, depth {snapshot.depth_force_cmd:+d}")
        self.pressed_buttons_field.setText(str(list(snapshot.pressed_buttons)))

        self.depth_target_field.setText(str(snapshot.depth_target))
        self.gripper_pwm_field.setText("--" if snapshot.gripper_pwm is None else str(snapshot.gripper_pwm))
        self.gimbal_tilt_field.setText(str(snapshot.gimbal_tilt_pwm))
        self.gimbal_pan_field.setText(str(snapshot.gimbal_pan_pwm))
        self.last_error_field.setText(snapshot.last_error or "None")

        self.send_log_edit.setPlainText("\n".join(snapshot.send_log))
        self.recv_log_edit.setPlainText("\n".join(snapshot.recv_log))
        self._update_runtime_state()

    def _on_joystick_status_message(self, message: str) -> None:
        """Show worker status messages in the status bar."""
        self.statusBar().showMessage(message, 5000)
        if "failed" in message.lower() or "error" in message.lower() or "unavailable" in message.lower():
            self.last_error_field.setText(message)

    def _update_runtime_state(self) -> None:
        """Enable or disable buttons so invalid actions are harder to trigger."""
        preview_running = self._video_client.is_previewing
        recording_running = self._video_client.is_recording
        joystick_running = self._joystick_worker is not None and self._joystick_worker.isRunning()

        self.start_preview_button.setEnabled(not preview_running)
        self.stop_preview_button.setEnabled(preview_running or self._preview_requested)
        self.single_capture_button.setEnabled(preview_running)
        self.start_auto_button.setEnabled(preview_running and not self._auto_capture_running)
        self.stop_auto_button.setEnabled(self._auto_capture_running)
        self.start_record_button.setEnabled(preview_running and not recording_running)
        self.stop_record_button.setEnabled(recording_running)
        self.start_joystick_button.setEnabled(PYGAME_AVAILABLE and not joystick_running)
        self.stop_joystick_button.setEnabled(joystick_running)

    def resizeEvent(self, event: QEvent) -> None:
        """Keep the video preview scaled correctly when the window size changes."""
        super().resizeEvent(event)
        self._render_current_frame()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop background work cleanly before the app exits."""
        self._stop_auto_capture()
        self._stop_recording()
        self._stop_preview()
        self._stop_joystick_backend()
        self._save_ui_settings()
        event.accept()
