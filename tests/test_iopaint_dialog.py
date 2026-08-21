from app.services.iopaint_cleanup import IOPaintConfiguration
from app.ui.iopaint_dialog import IOPaintSettingsDialog


def test_iopaint_dialog_round_trips_runtime_configuration(qapp) -> None:
    dialog = IOPaintSettingsDialog(
        IOPaintConfiguration(
            executable="/opt/iopaint",
            model="lama",
            device="cpu",
            operation_timeout_seconds=300,
        )
    )

    dialog.executable_edit.setText("/venv/bin/iopaint")
    dialog.model_edit.setText("lama")
    dialog.device_combo.setCurrentText("cuda")
    dialog.timeout_spin.setValue(420)

    assert dialog.configuration() == IOPaintConfiguration(
        executable="/venv/bin/iopaint",
        model="lama",
        device="cuda",
        operation_timeout_seconds=420,
    )
