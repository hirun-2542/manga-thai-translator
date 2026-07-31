"""Editor for one text block."""

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.models import (
    BlockStatus,
    SourceLanguage,
    TextBlock,
    WritingMode,
    utc_now,
)


class BlockEditor(QWidget):
    """Edit persisted fields without owning project-level actions."""

    block_changed = Signal(object)
    delete_requested = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._block: TextBlock | None = None

        self.source_language_combo = QComboBox()
        self.source_language_combo.addItem("Inherit from page/project", None)
        for language in SourceLanguage:
            self.source_language_combo.addItem(language.value, language.value)
        self.source_language_combo.setToolTip(
            "Override the source language for this block, or inherit the page/project setting."
        )

        self.writing_mode_combo = QComboBox()
        for mode in WritingMode:
            self.writing_mode_combo.addItem(mode.value.replace("_", " ").title(), mode.value)
        self.writing_mode_combo.setToolTip("Choose how the source text is written.")

        self.source_text_edit = QTextEdit()
        self.source_text_edit.setAcceptRichText(False)
        self.source_text_edit.setToolTip("Review and correct the recognized source text.")

        self.translated_text_edit = QTextEdit()
        self.translated_text_edit.setAcceptRichText(False)
        self.translated_text_edit.setToolTip("Review and edit the translated text.")

        self.speaker_edit = QLineEdit()
        self.speaker_edit.setToolTip("Optional speaker or character name.")

        self.note_edit = QTextEdit()
        self.note_edit.setAcceptRichText(False)
        self.note_edit.setToolTip("Optional note about this block or its translation.")

        self.status_combo = QComboBox()
        for status in BlockStatus:
            self.status_combo.addItem(status.value.replace("_", " ").title(), status.value)
        self.status_combo.setToolTip("Set the current review status for this block.")

        self.ocr_provider_edit = QLineEdit()
        self.ocr_provider_edit.setReadOnly(True)
        self.ocr_provider_edit.setPlaceholderText("Not assigned")
        self.ocr_provider_edit.setToolTip("OCR provider recorded for this block (read-only).")

        form = QFormLayout()
        form.addRow("Source language", self.source_language_combo)
        form.addRow("Writing mode", self.writing_mode_combo)
        form.addRow("Source text", self.source_text_edit)
        form.addRow("Translated text", self.translated_text_edit)
        form.addRow("Speaker", self.speaker_edit)
        form.addRow("Note", self.note_edit)
        form.addRow("Status", self.status_combo)
        form.addRow("OCR provider", self.ocr_provider_edit)

        self.delete_button = QPushButton("Delete block")
        self.delete_button.setToolTip("Delete the selected text block.")

        self.confirm_ocr_button = QPushButton("Confirm OCR")
        self.confirm_ocr_button.setAccessibleName("Confirm OCR")
        self.confirm_ocr_button.setToolTip("Mark the selected block's OCR text as reviewed.")

        self.confirm_translation_button = QPushButton("Confirm translation")
        self.confirm_translation_button.setAccessibleName("Confirm translation")
        self.confirm_translation_button.setToolTip(
            "Mark the selected block's translation as reviewed."
        )

        confirmation_layout = QHBoxLayout()
        confirmation_layout.addWidget(self.confirm_ocr_button)
        confirmation_layout.addWidget(self.confirm_translation_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(confirmation_layout)
        layout.addWidget(self.delete_button)
        layout.addStretch()

        for signal in (
            self.source_language_combo.currentIndexChanged,
            self.writing_mode_combo.currentIndexChanged,
            self.source_text_edit.textChanged,
            self.translated_text_edit.textChanged,
            self.speaker_edit.textChanged,
            self.note_edit.textChanged,
            self.status_combo.currentIndexChanged,
        ):
            signal.connect(self._emit_block_changed)
        self.status_combo.currentIndexChanged.connect(self._update_confirmation_buttons)
        self.confirm_ocr_button.clicked.connect(
            lambda: self._confirm_status(BlockStatus.OCR_REVIEWED)
        )
        self.confirm_translation_button.clicked.connect(
            lambda: self._confirm_status(BlockStatus.TRANSLATION_REVIEWED)
        )
        self.delete_button.clicked.connect(self._emit_delete_requested)
        self.set_block(None)

    def set_block(self, block: TextBlock | None) -> None:
        controls = (
            self.source_language_combo,
            self.writing_mode_combo,
            self.source_text_edit,
            self.translated_text_edit,
            self.speaker_edit,
            self.note_edit,
            self.status_combo,
            self.ocr_provider_edit,
        )
        blockers = [QSignalBlocker(control) for control in controls]
        self._block = block

        if block is None:
            self.source_language_combo.setCurrentIndex(0)
            self.writing_mode_combo.setCurrentIndex(0)
            self.source_text_edit.clear()
            self.translated_text_edit.clear()
            self.speaker_edit.clear()
            self.note_edit.clear()
            self.status_combo.setCurrentIndex(0)
            self.ocr_provider_edit.clear()
        else:
            self._set_combo_data(self.source_language_combo, block.source_language)
            self._set_combo_data(self.writing_mode_combo, block.writing_mode)
            self.source_text_edit.setPlainText(block.source_text)
            self.translated_text_edit.setPlainText(block.translated_text)
            self.speaker_edit.setText(block.speaker)
            self.note_edit.setPlainText(block.note)
            self._set_combo_data(self.status_combo, block.status)
            self.ocr_provider_edit.setText(block.ocr_provider or "")

        del blockers
        self.setEnabled(block is not None)
        self._update_confirmation_buttons()

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value.value if isinstance(value, str) else value)
        combo.setCurrentIndex(index)

    def _emit_block_changed(self, *_args: object) -> None:
        if self._block is None:
            return
        self._block = self._block.model_copy(
            update={
                "source_language": (
                    SourceLanguage(value)
                    if (value := self.source_language_combo.currentData()) is not None
                    else None
                ),
                "writing_mode": WritingMode(self.writing_mode_combo.currentData()),
                "source_text": self.source_text_edit.toPlainText(),
                "translated_text": self.translated_text_edit.toPlainText(),
                "speaker": self.speaker_edit.text(),
                "note": self.note_edit.toPlainText(),
                "status": BlockStatus(self.status_combo.currentData()),
                "updated_at": utc_now(),
            }
        )
        self.block_changed.emit(self._block)

    def _update_confirmation_buttons(self, *_args: object) -> None:
        status = self.status_combo.currentData()
        self.confirm_ocr_button.setEnabled(
            self._block is not None
            and status in (BlockStatus.OCR_COMPLETE, BlockStatus.OCR_REVIEWED)
        )
        self.confirm_translation_button.setEnabled(
            self._block is not None
            and status in (BlockStatus.TRANSLATED, BlockStatus.TRANSLATION_REVIEWED)
        )

    def _confirm_status(self, status: BlockStatus) -> None:
        if self._block is None:
            return
        with QSignalBlocker(self.status_combo):
            self._set_combo_data(self.status_combo, status)
        self._update_confirmation_buttons()
        self._emit_block_changed()

    def _emit_delete_requested(self) -> None:
        if self._block is not None:
            self.delete_requested.emit(self._block.id)
