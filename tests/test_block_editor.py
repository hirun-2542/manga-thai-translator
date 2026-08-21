from uuid import uuid4

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

import app.ui.block_editor as block_editor_module
from app.core.models import (
    BlockStatus,
    BoundingBox,
    SourceLanguage,
    TextAlignment,
    TextBlock,
    WritingMode,
)
from app.services.export import RenderMetric
from app.ui.block_editor import BlockEditor


@pytest.fixture(autouse=True)
def deterministic_font_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        block_editor_module,
        "thai_font_families",
        lambda: ("Test Thai", "Other Thai"),
    )
    monkeypatch.setattr(
        block_editor_module,
        "font_styles",
        lambda family: ("Regular", "Bold") if family == "Test Thai" else ("Regular",),
    )


def make_block() -> TextBlock:
    return TextBlock(
        page_id=uuid4(),
        bbox=BoundingBox(x=10, y=20, width=30, height=40),
        reading_order=7,
        source_language=SourceLanguage.JA,
        writing_mode=WritingMode.VERTICAL,
        source_text="ただいま",
        translated_text="กลับมาแล้ว",
        typesetting_alignment=TextAlignment.RIGHT,
        typesetting_font_size=42,
        typesetting_line_spacing=9,
        rotation_degrees=12.5,
        mirror_horizontal=True,
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
    assert editor.alignment_combo.currentData() == TextAlignment.RIGHT.value
    assert editor.stroke_width_spin.value() == 0
    assert editor.font_size_spin.value() == 42
    assert editor.rotation_spin.value() == 12.5
    assert editor.mirror_horizontal_check.isChecked()
    assert not editor.mirror_vertical_check.isChecked()
    assert editor.line_spacing_spin.value() == 9
    assert editor.speaker_edit.text() == "ฮารุ"
    assert editor.note_edit.toPlainText() == "ตรวจคำลงท้าย"
    assert editor.status_combo.currentData() == BlockStatus.TRANSLATED.value
    assert editor.ocr_provider_edit.text() == "manga-ocr"
    assert editor.ocr_provider_edit.isReadOnly()
    assert not editor.confirm_ocr_button.isEnabled()
    assert editor.confirm_translation_button.isEnabled()
    assert editor.confirm_ocr_button.accessibleName() == "Confirm OCR"
    assert editor.confirm_translation_button.accessibleName() == "Confirm Translation"
    assert editor.confirm_ocr_button.toolTip()
    assert editor.confirm_translation_button.toolTip()
    assert changed == []

    editor.set_block(None)

    assert not editor.isEnabled()
    assert editor.source_text_edit.toPlainText() == ""
    assert editor.alignment_combo.currentData() == TextAlignment.CENTER.value
    assert editor.font_size_spin.value() == 0
    assert editor.stroke_width_spin.value() == 0
    assert editor.rotation_spin.value() == 0.0
    assert not editor.mirror_horizontal_check.isChecked()
    assert not editor.mirror_vertical_check.isChecked()
    assert editor.line_spacing_spin.value() == -1
    assert editor.ocr_provider_edit.text() == ""
    assert not editor.confirm_ocr_button.isEnabled()
    assert not editor.confirm_translation_button.isEnabled()
    assert changed == []


def test_layout_bbox_controls_preserve_position_and_clamp_to_image_bounds(qapp) -> None:
    editor = BlockEditor()
    block = make_block().model_copy(update={"bbox": BoundingBox(x=60, y=50, width=30, height=40)})
    changed = []
    editor.block_changed.connect(changed.append)
    editor.set_image_bounds(100, 80)
    editor.set_block(block)

    assert editor.width_spin.value() == 30
    assert editor.height_spin.value() == 30
    assert editor.width_spin.suffix() == " px"
    assert editor.height_spin.suffix() == " px"

    editor.width_spin.setValue(999)
    editor.height_spin.setValue(1)
    payload = changed[-1]
    assert payload.bbox.x == 60
    assert payload.bbox.y == 50
    assert payload.bbox.width == 40
    assert payload.bbox.height == 5


def test_color_swatches_show_auto_exact_color_and_picker_state(qapp, monkeypatch) -> None:
    editor = BlockEditor()
    editor.set_block(make_block())
    assert editor.fill_color_button.text() == "Auto"
    assert editor.stroke_color_button.text() == "Auto"
    assert editor.fill_color_button.accessibleDescription() == "Automatic color selection"

    editor.fill_color_edit.setText("#102030")
    editor.fill_color_edit.editingFinished.emit()
    assert editor.fill_color_button.text() == "#102030"
    assert "#102030" in editor.fill_color_button.accessibleDescription()

    editor.fill_color_edit.setText("#123")
    editor.fill_color_edit.editingFinished.emit()
    assert editor.fill_color_edit.text() == "#102030"
    assert editor.fill_color_button.text() == "#102030"

    editor.fill_color_edit.clear()
    editor.fill_color_edit.editingFinished.emit()
    assert editor.fill_color_button.text() == "Auto"

    monkeypatch.setattr(
        block_editor_module.QColorDialog,
        "getColor",
        lambda *_args: QColor("#A0B0C0"),
    )
    editor.fill_color_button.click()
    assert editor.fill_color_button.text() == "#A0B0C0"

    editor.set_block(None)
    assert editor.fill_color_button.text() == "Auto"
    assert editor.stroke_color_button.text() == "Auto"


def test_context_tabs_group_fields_and_survive_block_selection(qapp) -> None:
    editor = BlockEditor()
    first = make_block()
    second = make_block().model_copy(update={"status": BlockStatus.ERROR})

    assert editor.tabs.widget(0) is editor.text_tab
    assert editor.tabs.widget(1) is editor.layout_tab
    assert editor.tabs.widget(2) is editor.details_tab
    assert editor.source_text_edit.parentWidget() is not editor.layout_tab
    assert editor.font_size_spin.parentWidget() is not editor.text_tab
    assert editor.status_combo.parentWidget() is not editor.text_tab

    editor.set_block(first)
    editor.tabs.setCurrentIndex(1)
    editor.set_block(second)

    assert editor.tabs.currentIndex() == 1
    assert editor.tabs.tabText(2) == "Details ⚠"


def test_context_action_signals_keep_block_identity(qapp) -> None:
    editor = BlockEditor()
    block = make_block()
    ocr_requested = []
    translate_requested = []
    editor.ocr_requested.connect(ocr_requested.append)
    editor.translate_requested.connect(translate_requested.append)
    editor.set_block(block)

    editor.ocr_button.click()
    editor.translate_button.click()

    assert ocr_requested == [block.id]
    assert translate_requested == [block.id]


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
    editor.alignment_combo.setCurrentIndex(
        editor.alignment_combo.findData(TextAlignment.LEFT.value)
    )
    editor.font_size_spin.setValue(36)
    editor.rotation_spin.setValue(-8.0)
    editor.mirror_horizontal_check.setChecked(False)
    editor.mirror_vertical_check.setChecked(True)
    editor.line_spacing_spin.setValue(14)
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
    assert payload.typesetting_alignment is TextAlignment.LEFT
    assert payload.typesetting_font_size == 36
    assert payload.rotation_degrees == -8.0
    assert payload.mirror_horizontal is False
    assert payload.mirror_vertical is True
    assert payload.typesetting_line_spacing == 14
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


def test_auto_font_size_emits_none(qapp) -> None:
    editor = BlockEditor()
    block = make_block()
    changed = []
    editor.block_changed.connect(changed.append)
    editor.set_block(block)

    editor.font_size_spin.setValue(0)

    assert changed[-1].typesetting_font_size is None


def test_auto_line_spacing_emits_none_and_zero_is_manual(qapp) -> None:
    editor = BlockEditor()
    block = make_block()
    changed = []
    editor.block_changed.connect(changed.append)
    editor.set_block(block)

    editor.line_spacing_spin.setValue(-1)
    assert changed[-1].typesetting_line_spacing is None

    editor.line_spacing_spin.setValue(0)
    assert changed[-1].typesetting_line_spacing == 0


def test_typesetting_override_controls_emit_and_reset(qapp) -> None:
    editor = BlockEditor()
    block = make_block()
    changed = []
    editor.block_changed.connect(changed.append)
    editor.set_block(block)

    family_index = editor.font_family_combo.findData("Test Thai")
    assert family_index >= 0
    editor.font_family_combo.setCurrentIndex(family_index)
    style_index = editor.font_style_combo.findData("Bold")
    assert style_index >= 0
    editor.font_style_combo.setCurrentIndex(style_index)
    editor.fill_color_edit.setText("#102030")
    editor.fill_color_edit.editingFinished.emit()
    editor.stroke_color_edit.setText("#A0B0C0")
    editor.stroke_color_edit.editingFinished.emit()
    editor.stroke_width_spin.setValue(0)

    payload = changed[-1]
    assert payload.typesetting_font_family == "Test Thai"
    assert payload.typesetting_font_style == "Bold"
    assert payload.typesetting_fill_color == "#102030"
    assert payload.typesetting_stroke_color == "#A0B0C0"
    assert payload.typesetting_stroke_width == 0

    editor.set_block(None)
    assert editor.font_family_combo.currentData() is None
    assert editor.font_style_combo.currentData() is None
    assert editor.fill_color_edit.text() == ""
    assert editor.stroke_color_edit.text() == ""
    assert editor.stroke_width_spin.value() == 0


def test_legacy_auto_outline_is_displayed_and_emitted_as_zero(qapp) -> None:
    editor = BlockEditor()
    block = make_block().model_copy(update={"typesetting_stroke_width": None})
    changed = []
    editor.block_changed.connect(changed.append)

    editor.set_block(block)

    assert editor.stroke_width_spin.minimum() == 0
    assert editor.stroke_width_spin.specialValueText() == ""
    assert editor.stroke_width_spin.value() == 0

    editor.stroke_width_spin.setValue(3)
    assert changed[-1].typesetting_stroke_width == 3

    editor.stroke_width_spin.setValue(0)
    editor.translated_text_edit.setPlainText("แก้ไขแล้ว")

    assert changed[-1].typesetting_stroke_width == 0


@pytest.mark.parametrize("alias", ("auto", "อัตโนมัติ"))
def test_text_override_aliases_clear_color_overrides(qapp, alias: str) -> None:
    editor = BlockEditor()
    block = make_block().model_copy(
        update={
            "typesetting_fill_color": "#102030",
            "typesetting_stroke_color": "#A0B0C0",
        }
    )
    changed = []
    editor.block_changed.connect(changed.append)
    editor.set_block(block)

    for field in (editor.fill_color_edit, editor.stroke_color_edit):
        field.clear()
        field.insert(alias)
        assert field.hasAcceptableInput()

    editor.fill_color_edit.editingFinished.emit()
    editor.stroke_color_edit.editingFinished.emit()

    assert changed[-1].typesetting_fill_color is None
    assert changed[-1].typesetting_stroke_color is None


def test_font_search_reverts_unselected_text_and_emits_once(qapp) -> None:
    editor = BlockEditor()
    editor.set_block(make_block())
    changed = []
    editor.block_changed.connect(changed.append)

    assert editor.font_family_combo.completer().filterMode() == Qt.MatchFlag.MatchContains
    editor.font_family_combo.setEditText("Thai")
    assert changed == []
    editor.font_family_combo.lineEdit().editingFinished.emit()
    assert changed == []
    assert editor.font_family_combo.currentData() is None
    assert editor.font_family_combo.currentText() == "Auto"

    family_index = editor.font_family_combo.findData("Test Thai")
    editor.font_family_combo.setCurrentIndex(family_index)
    assert len(changed) == 1
    assert changed[-1].typesetting_font_family == "Test Thai"

    editor.font_family_combo.setEditText("Test")
    editor.font_family_combo.lineEdit().editingFinished.emit()
    assert len(changed) == 1
    assert editor.font_family_combo.currentData() == "Test Thai"
    assert editor.font_family_combo.currentText() == "Test Thai"


@pytest.mark.parametrize("alias", ("auto", "อัตโนมัติ"))
def test_font_family_auto_alias_clears_existing_override(qapp, alias: str) -> None:
    editor = BlockEditor()
    block = make_block().model_copy(update={"typesetting_font_family": "Test Thai"})
    changed = []
    editor.block_changed.connect(changed.append)
    editor.set_block(block)

    editor.font_family_combo.setEditText(alias)
    editor.font_family_combo.lineEdit().editingFinished.emit()

    assert editor.font_family_combo.currentData() is None
    assert changed[-1].typesetting_font_family is None


def test_missing_font_override_is_marked_and_preserved_until_replaced(qapp) -> None:
    block = make_block().model_copy(
        update={
            "typesetting_font_family": "Missing Thai",
            "typesetting_font_style": "Black",
        }
    )
    editor = BlockEditor()
    editor.set_block(block)
    changed = []
    editor.block_changed.connect(changed.append)

    assert editor.font_family_combo.currentData() == "Missing Thai"
    assert "not available locally" in editor.font_family_combo.currentText()
    assert editor.font_style_combo.currentData() == "Black"
    assert "not available locally" in editor.font_style_combo.currentText()

    editor.translated_text_edit.setPlainText("แก้ไขแล้ว")
    assert changed[-1].typesetting_font_family == "Missing Thai"
    assert changed[-1].typesetting_font_style == "Black"

    editor.font_family_combo.setCurrentIndex(0)
    assert changed[-1].typesetting_font_family is None
    assert changed[-1].typesetting_font_style is None

    installed_index = editor.font_family_combo.findData("Test Thai")
    editor.font_family_combo.setCurrentIndex(installed_index)
    style_index = editor.font_style_combo.findData("Bold")
    editor.font_style_combo.setCurrentIndex(style_index)
    assert changed[-1].typesetting_font_family == "Test Thai"
    assert changed[-1].typesetting_font_style == "Bold"


def test_color_input_reverts_invalid_values_and_uses_native_picker(
    qapp,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    editor = BlockEditor()
    editor.set_block(make_block())
    changed = []
    editor.block_changed.connect(changed.append)

    editor.fill_color_edit.setText("#123")
    editor.fill_color_edit.editingFinished.emit()
    assert editor.fill_color_edit.text() == ""
    assert changed == []

    monkeypatch.setattr(
        block_editor_module.QColorDialog,
        "getColor",
        lambda *_args: QColor("#102030"),
    )
    editor.fill_color_button.click()

    assert len(changed) == 1
    assert editor.fill_color_edit.text() == "#102030"
    assert changed[-1].typesetting_fill_color == "#102030"


def test_runtime_render_metrics_display_and_reset(qapp) -> None:
    editor = BlockEditor()
    block = make_block()
    editor.set_block(block)

    assert editor.thai_font_edit.isReadOnly()
    assert editor.effective_font_edit.isReadOnly()
    assert editor.effective_stroke_width_edit.isReadOnly()
    assert editor.effective_font_size_edit.isReadOnly()
    assert editor.effective_fill_edit.isReadOnly()
    assert editor.effective_stroke_edit.isReadOnly()
    assert editor.effective_font_size_edit.text() == "Auto"
    assert editor.effective_fill_edit.text() == "Auto"
    assert editor.effective_stroke_edit.text() == "Auto"

    editor.set_thai_font_label("NotoSansThai-Regular.ttf")
    editor.set_render_metric(RenderMetric(block.id, 31, (1, 2, 3), (250, 128, 0)))

    assert editor.thai_font_edit.text() == "NotoSansThai-Regular.ttf"
    assert editor.effective_font_size_edit.text() == "31 px"
    assert editor.effective_fill_edit.text() == "#010203"
    assert editor.effective_stroke_edit.text() == "#FA8000"
    assert editor.effective_font_edit.text() == "Auto"
    assert editor.effective_stroke_width_edit.text() == "0 px"

    editor.set_render_metric(RenderMetric(block.id, 31, (1, 2, 3), None))
    assert editor.effective_stroke_edit.text() == "None"

    editor.set_block(None)
    assert editor.effective_font_size_edit.text() == "Auto"
    assert editor.effective_fill_edit.text() == "Auto"
    assert editor.effective_stroke_edit.text() == "Auto"


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
