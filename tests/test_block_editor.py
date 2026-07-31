from uuid import uuid4

from app.core.models import (
    BlockStatus,
    BoundingBox,
    SourceLanguage,
    TextBlock,
    WritingMode,
)
from app.ui.block_editor import BlockEditor


def make_block() -> TextBlock:
    return TextBlock(
        page_id=uuid4(),
        bbox=BoundingBox(x=10, y=20, width=30, height=40),
        reading_order=7,
        source_language=SourceLanguage.JA,
        writing_mode=WritingMode.VERTICAL,
        source_text="ただいま",
        translated_text="กลับมาแล้ว",
        ocr_provider="manga-ocr",
        speaker="ฮารุ",
        note="ตรวจคำลงท้าย",
        status=BlockStatus.TRANSLATED,
    )


def test_set_block_populates_and_clear_disables_without_emitting(qapp) -> None:
    editor = BlockEditor()
    changed = []
    editor.block_changed.connect(changed.append)
    block = make_block()

    editor.set_block(block)

    assert editor.isEnabled()
    assert editor.source_language_combo.currentData() == SourceLanguage.JA.value
    assert editor.writing_mode_combo.currentData() == WritingMode.VERTICAL.value
    assert editor.source_text_edit.toPlainText() == "ただいま"
    assert editor.translated_text_edit.toPlainText() == "กลับมาแล้ว"
    assert editor.speaker_edit.text() == "ฮารุ"
    assert editor.note_edit.toPlainText() == "ตรวจคำลงท้าย"
    assert editor.status_combo.currentData() == BlockStatus.TRANSLATED.value
    assert editor.ocr_provider_edit.text() == "manga-ocr"
    assert editor.ocr_provider_edit.isReadOnly()
    assert not editor.confirm_ocr_button.isEnabled()
    assert editor.confirm_translation_button.isEnabled()
    assert editor.confirm_ocr_button.accessibleName() == "Confirm OCR"
    assert editor.confirm_translation_button.accessibleName() == "Confirm translation"
    assert editor.confirm_ocr_button.toolTip()
    assert editor.confirm_translation_button.toolTip()
    assert changed == []

    editor.set_block(None)

    assert not editor.isEnabled()
    assert editor.source_text_edit.toPlainText() == ""
    assert editor.ocr_provider_edit.text() == ""
    assert not editor.confirm_ocr_button.isEnabled()
    assert not editor.confirm_translation_button.isEnabled()
    assert changed == []


def test_edits_emit_updated_copy_preserving_block_identity(qapp) -> None:
    editor = BlockEditor()
    block = make_block()
    changed = []
    editor.block_changed.connect(changed.append)
    editor.set_block(block)

    editor.source_language_combo.setCurrentIndex(
        editor.source_language_combo.findData(SourceLanguage.KO)
    )
    editor.writing_mode_combo.setCurrentIndex(
        editor.writing_mode_combo.findData(WritingMode.HORIZONTAL)
    )
    editor.source_text_edit.setPlainText("다녀왔어")
    editor.translated_text_edit.setPlainText("กลับมาแล้วเหรอ")
    editor.speaker_edit.setText("มิน")
    editor.note_edit.setPlainText("กันเอง")
    editor.status_combo.setCurrentIndex(
        editor.status_combo.findData(BlockStatus.TRANSLATION_REVIEWED)
    )

    payload = changed[-1]
    assert payload is not block
    assert payload.id == block.id
    assert payload.page_id == block.page_id
    assert payload.bbox == block.bbox
    assert payload.reading_order == block.reading_order
    assert payload.source_language is SourceLanguage.KO
    assert payload.writing_mode is WritingMode.HORIZONTAL
    assert payload.source_text == "다녀왔어"
    assert payload.translated_text == "กลับมาแล้วเหรอ"
    assert payload.speaker == "มิน"
    assert payload.note == "กันเอง"
    assert payload.status is BlockStatus.TRANSLATION_REVIEWED
    assert payload.updated_at > block.updated_at


def test_inherit_language_and_delete_signal(qapp) -> None:
    editor = BlockEditor()
    block = make_block()
    changed = []
    deleted = []
    editor.block_changed.connect(changed.append)
    editor.delete_requested.connect(deleted.append)
    editor.set_block(block)

    editor.source_language_combo.setCurrentIndex(0)
    editor.delete_button.click()

    assert changed[-1].source_language is None
    assert deleted == [block.id]


def test_confirmation_button_states_follow_manual_status_changes(qapp) -> None:
    editor = BlockEditor()
    block = make_block().model_copy(update={"status": BlockStatus.DETECTED})
    editor.set_block(block)

    assert not editor.confirm_ocr_button.isEnabled()
    assert not editor.confirm_translation_button.isEnabled()

    editor.status_combo.setCurrentIndex(editor.status_combo.findData(BlockStatus.OCR_COMPLETE))
    assert editor.confirm_ocr_button.isEnabled()
    assert not editor.confirm_translation_button.isEnabled()

    editor.status_combo.setCurrentIndex(editor.status_combo.findData(BlockStatus.TRANSLATED))
    assert not editor.confirm_ocr_button.isEnabled()
    assert editor.confirm_translation_button.isEnabled()


def test_confirmation_buttons_emit_one_updated_copy_with_stable_data(qapp) -> None:
    cases = (
        ("confirm_ocr_button", BlockStatus.OCR_COMPLETE, BlockStatus.OCR_REVIEWED),
        (
            "confirm_translation_button",
            BlockStatus.TRANSLATED,
            BlockStatus.TRANSLATION_REVIEWED,
        ),
    )

    for button_name, initial_status, confirmed_status in cases:
        editor = BlockEditor()
        block = make_block().model_copy(update={"status": initial_status})
        changed = []
        editor.block_changed.connect(changed.append)
        editor.set_block(block)

        getattr(editor, button_name).click()

        assert len(changed) == 1
        payload = changed[0]
        assert payload is not block
        assert payload.id == block.id
        assert payload.status is confirmed_status
        assert payload.updated_at > block.updated_at
        assert payload.model_dump(exclude={"status", "updated_at"}) == block.model_dump(
            exclude={"status", "updated_at"}
        )
        assert getattr(editor, button_name).isEnabled()

        getattr(editor, button_name).click()

        assert len(changed) == 2
        assert changed[1].status is confirmed_status
        assert changed[1].updated_at > payload.updated_at
