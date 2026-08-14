"""Runtime-only IOPaint settings dialog."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

from app.services.iopaint_cleanup import IOPaintConfiguration


class IOPaintSettingsDialog(QDialog):
    def __init__(
        self,
        configuration: IOPaintConfiguration,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("IOPaint Settings")

        self.executable_edit = QLineEdit(configuration.executable)
        self.executable_edit.setToolTip("Executable name or absolute path, such as iopaint.")
        self.model_edit = QLineEdit(configuration.model)
        self.model_edit.setToolTip("IOPaint model passed to --model, such as lama.")
        self.device_combo = QComboBox()
        self.device_combo.setEditable(True)
        self.device_combo.addItems(("cpu", "cuda", "mps"))
        self.device_combo.setCurrentText(configuration.device)
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(1.0, 86400.0)
        self.timeout_spin.setDecimals(1)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.setValue(configuration.operation_timeout_seconds)
        self.timeout_spin.setToolTip("Maximum time for one IOPaint CLI cleanup operation.")

        form = QFormLayout()
        form.addRow("Executable", self.executable_edit)
        form.addRow("Model", self.model_edit)
        form.addRow("Device", self.device_combo)
        form.addRow("Operation timeout", self.timeout_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("OK")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def configuration(self) -> IOPaintConfiguration:
        return IOPaintConfiguration(
            executable=self.executable_edit.text().strip(),
            model=self.model_edit.text().strip(),
            device=self.device_combo.currentText().strip(),
            operation_timeout_seconds=self.timeout_spin.value(),
        )
