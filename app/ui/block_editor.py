"""Editor for one text block."""

import os

from pydantic import ValidationError
from PySide6.QtCore import QRegularExpression, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core.models import (
    BlockStatus,
    BoundingBox,
    SourceLanguage,
    TextAlignment,
    TextBlock,
    WritingMode,
    utc_now,
)
from app.services.export import RenderMetric
from app.services.system_fonts import font_styles, thai_font_families
from app.ui.image_viewer import MIN_BLOCK_SIZE

_HEX_COLOR_PATTERN = QRegularExpression(r"^(?:|#[0-9A-Fa-f]{6}|[Aa][Uu][Tt][Oo]|อัตโนมัติ)$")

_WRITING_MODE_LABELS = {
    WritingMode.HORIZONTAL: "Horizontal",
    WritingMode.VERTICAL: "Vertical",
}
_ALIGNMENT_LABELS = {
    TextAlignment.LEFT: "Left",
    TextAlignment.CENTER: "Center",
    TextAlignment.RIGHT: "Right",
}
_STATUS_LABELS = {
    BlockStatus.DETECTED: "Detected",
    BlockStatus.LANGUAGE_REVIEW_REQUIRED: "Language review required",
    BlockStatus.OCR_COMPLETE: "OCR complete",
    BlockStatus.OCR_REVIEWED: "OCR reviewed",
    BlockStatus.TRANSLATED: "Translated",
    BlockStatus.TRANSLATION_REVIEWED: "Translation reviewed",
    BlockStatus.ERROR: "Error",
}
_AUTO = "Auto"
_AUTO_ALIASES = frozenset({"auto", "อัตโนมัติ".casefold()})


class BlockEditor(QWidget):
    """Edit persisted fields without owning project-level actions."""

    block_changed = Signal(object)
    delete_requested = Signal(object)
    ocr_requested = Signal(object)
    translate_requested = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._block: TextBlock | None = None
        self._image_width: float | None = None
        self._image_height: float | None = None

        self.source_language_combo = QComboBox()
        self.source_language_combo.addItem("Inherit from page/project", None)
        for language in SourceLanguage:
            self.source_language_combo.addItem(language.value, language.value)
        self.source_language_combo.setToolTip(
            "Choose a source-language override for this block, or inherit the page/project setting."
        )

        self.writing_mode_combo = QComboBox()
        for mode in WritingMode:
            self.writing_mode_combo.addItem(_WRITING_MODE_LABELS[mode], mode.value)
        self.writing_mode_combo.setToolTip("Choose how the source text is written.")

        self.source_text_edit = QTextEdit()
        self.source_text_edit.setAcceptRichText(False)
        self.source_text_edit.setTabChangesFocus(True)
        self.source_text_edit.setToolTip("Review and correct the OCR text.")

        self.translated_text_edit = QTextEdit()
        self.translated_text_edit.setAcceptRichText(False)
        self.translated_text_edit.setTabChangesFocus(True)
        self.translated_text_edit.setToolTip("Review and edit the Thai translation.")

        self.alignment_combo = QComboBox()
        for alignment in TextAlignment:
            self.alignment_combo.addItem(_ALIGNMENT_LABELS[alignment], alignment.value)
        self.alignment_combo.setToolTip("Align Thai text within the block.")

        self.font_family_combo = QComboBox()
        self.font_family_combo.setEditable(True)
        self.font_family_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.font_family_combo.setToolTip("Choose a Thai font family for this block.")
        self.font_family_combo.addItem(_AUTO, None)
        for family in thai_font_families():
            self.font_family_combo.addItem(family, family)
        if self.font_family_combo.completer() is not None:
            self.font_family_combo.completer().setCaseSensitivity(
                Qt.CaseSensitivity.CaseInsensitive
            )
            self.font_family_combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)

        self.font_style_combo = QComboBox()
        self.font_style_combo.setToolTip("Choose a Thai font style for this block.")
        self.font_style_combo.addItem(_AUTO, None)

        self.fill_color_edit = QLineEdit()
        self.fill_color_edit.setPlaceholderText(f"{_AUTO} (#RRGGBB)")
        self.fill_color_edit.setToolTip(
            "Set the text color as RGB, such as #000000, or leave blank for automatic selection."
        )
        self.fill_color_edit.setValidator(
            QRegularExpressionValidator(_HEX_COLOR_PATTERN, self.fill_color_edit)
        )

        self.stroke_color_edit = QLineEdit()
        self.stroke_color_edit.setPlaceholderText(f"{_AUTO} (#RRGGBB)")
        self.stroke_color_edit.setToolTip(
            "Set the outline color as RGB, such as #FFFFFF, or leave blank for automatic selection."
        )
        self.stroke_color_edit.setValidator(
            QRegularExpressionValidator(_HEX_COLOR_PATTERN, self.stroke_color_edit)
        )

        self.fill_color_button = self._color_button(
            self.fill_color_edit,
            "typesetting_fill_color",
            "Choose Thai text color",
        )
        self.stroke_color_button = self._color_button(
            self.stroke_color_edit,
            "typesetting_stroke_color",
            "Choose Thai text outline color",
        )

        self.stroke_width_spin = QSpinBox()
        self.stroke_width_spin.setRange(0, 32)
        self.stroke_width_spin.setSuffix(" px")
        self.stroke_width_spin.setToolTip(
            "Set the outline width in pixels; 0 disables the outline."
        )

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(MIN_BLOCK_SIZE, 1_000_000)
        self.width_spin.setDecimals(1)
        self.width_spin.setSingleStep(1.0)
        self.width_spin.setSuffix(" px")
        self.width_spin.setToolTip("Set the block width in image pixels.")

        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(MIN_BLOCK_SIZE, 1_000_000)
        self.height_spin.setDecimals(1)
        self.height_spin.setSingleStep(1.0)
        self.height_spin.setSuffix(" px")
        self.height_spin.setToolTip("Set the block height in image pixels.")

        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(0, 256)
        self.font_size_spin.setSpecialValueText(_AUTO)
        self.font_size_spin.setSuffix(" px")
        self.font_size_spin.setToolTip("Set a Thai font size, or use automatic sizing to fit.")

        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(-180.0, 180.0)
        self.rotation_spin.setDecimals(1)
        self.rotation_spin.setSingleStep(1.0)
        self.rotation_spin.setSuffix("°")
        self.rotation_spin.setToolTip(
            "Rotate the entire text region clockwise for selection, cleanup, preview, and export."
        )

        self.mirror_horizontal_check = QCheckBox("Horizontal")
        self.mirror_horizontal_check.setToolTip(
            "Mirror only the Thai text along the horizontal axis within the region."
        )
        self.mirror_vertical_check = QCheckBox("Vertical")
        self.mirror_vertical_check.setToolTip(
            "Mirror only the Thai text along the vertical axis within the region."
        )

        self.line_spacing_spin = QSpinBox()
        self.line_spacing_spin.setRange(-1, 256)
        self.line_spacing_spin.setSpecialValueText(_AUTO)
        self.line_spacing_spin.setSuffix(" px")
        self.line_spacing_spin.setToolTip("Set additional line spacing, or use automatic spacing.")

        self.speaker_edit = QLineEdit()
        self.speaker_edit.setToolTip("Optional speaker or character name.")

        self.note_edit = QTextEdit()
        self.note_edit.setAcceptRichText(False)
        self.note_edit.setTabChangesFocus(True)
        self.note_edit.setToolTip("Optional note about this block or its translation.")

        self.status_combo = QComboBox()
        for status in BlockStatus:
            self.status_combo.addItem(_STATUS_LABELS[status], status.value)
        self.status_combo.setToolTip("Set the current review status for this block.")

        self.ocr_provider_edit = QLineEdit()
        self.ocr_provider_edit.setReadOnly(True)
        self.ocr_provider_edit.setPlaceholderText("Not assigned")
        self.ocr_provider_edit.setToolTip("OCR provider recorded for this block (read-only).")

        self.thai_font_edit = self._runtime_field(
            "Global Thai fallback font",
            "Fallback Thai font used for preview and export (read-only).",
        )
        self.effective_font_edit = self._runtime_field(
            "Effective Thai font",
            "Font family and style used by the renderer for this block (read-only).",
        )
        self.effective_font_size_edit = self._runtime_field(
            "Effective Thai font size",
            "Font size selected by the renderer for this block (read-only).",
        )
        self.effective_fill_edit = self._runtime_field(
            "Effective text color",
            "RGB text color used by the renderer for this block (read-only).",
        )
        self.effective_stroke_edit = self._runtime_field(
            "Effective outline color",
            "Outline color used by the renderer, or no outline (read-only).",
        )
        self.effective_stroke_width_edit = self._runtime_field(
            "Effective outline width",
            "Outline width used by the renderer in pixels (read-only).",
        )
        self.set_thai_font_label(None)
        self._clear_render_metric()

        text_form = QFormLayout()
        text_form.addRow("Source language", self.source_language_combo)
        text_form.addRow("Writing mode", self.writing_mode_combo)
        text_form.addRow("OCR text", self.source_text_edit)
        text_form.addRow("Thai translation", self.translated_text_edit)

        layout_form = QFormLayout()
        layout_form.addRow("Width", self.width_spin)
        layout_form.addRow("Height", self.height_spin)
        layout_form.addRow("Alignment", self.alignment_combo)
        layout_form.addRow("Thai font family", self.font_family_combo)
        layout_form.addRow("Thai font style", self.font_style_combo)
        fill_color_row = QHBoxLayout()
        fill_color_row.setContentsMargins(0, 0, 0, 0)
        fill_color_row.addWidget(self.fill_color_edit, 1)
        fill_color_row.addWidget(self.fill_color_button)
        layout_form.addRow("Thai text color", fill_color_row)
        stroke_color_row = QHBoxLayout()
        stroke_color_row.setContentsMargins(0, 0, 0, 0)
        stroke_color_row.addWidget(self.stroke_color_edit, 1)
        stroke_color_row.addWidget(self.stroke_color_button)
        layout_form.addRow("Outline color", stroke_color_row)
        layout_form.addRow("Outline width", self.stroke_width_spin)
        layout_form.addRow("Thai font size", self.font_size_spin)
        layout_form.addRow("Region rotation", self.rotation_spin)
        mirror_row = QHBoxLayout()
        mirror_row.setContentsMargins(0, 0, 0, 0)
        mirror_row.addWidget(self.mirror_horizontal_check)
        mirror_row.addWidget(self.mirror_vertical_check)
        layout_form.addRow("Mirror Thai text", mirror_row)
        layout_form.addRow("Line spacing", self.line_spacing_spin)
        layout_form.addRow("Global fallback font", self.thai_font_edit)
        layout_form.addRow("Effective font", self.effective_font_edit)
        layout_form.addRow("Effective size", self.effective_font_size_edit)
        layout_form.addRow("Effective text color", self.effective_fill_edit)
        layout_form.addRow("Effective outline color", self.effective_stroke_edit)
        layout_form.addRow("Effective outline width", self.effective_stroke_width_edit)

        details_form = QFormLayout()
        details_form.addRow("Speaker", self.speaker_edit)
        details_form.addRow("Note", self.note_edit)
        details_form.addRow("Status", self.status_combo)
        details_form.addRow("OCR Provider", self.ocr_provider_edit)

        self.delete_button = QPushButton("Delete Block")
        self.delete_button.setToolTip("Delete the selected text block.")

        self.ocr_button = QPushButton("OCR Block")
        self.ocr_button.setToolTip("Run OCR again for the selected text block.")

        self.translate_button = QPushButton("Translate Block")
        self.translate_button.setToolTip("Translate the selected text block.")

        self.confirm_ocr_button = QPushButton("Confirm OCR")
        self.confirm_ocr_button.setAccessibleName("Confirm OCR")
        self.confirm_ocr_button.setToolTip("Mark the selected block's OCR text as reviewed.")

        self.confirm_translation_button = QPushButton("Confirm Translation")
        self.confirm_translation_button.setAccessibleName("Confirm Translation")
        self.confirm_translation_button.setToolTip(
            "Mark the selected block's translation as reviewed."
        )

        text_actions = QHBoxLayout()
        text_actions.addWidget(self.ocr_button)
        text_actions.addWidget(self.translate_button)
        confirmation_layout = QHBoxLayout()
        confirmation_layout.addWidget(self.confirm_ocr_button)
        confirmation_layout.addWidget(self.confirm_translation_button)

        text_content = QWidget()
        text_layout = QVBoxLayout(text_content)
        text_layout.addLayout(text_form)
        text_layout.addLayout(text_actions)
        text_layout.addLayout(confirmation_layout)
        text_layout.addStretch()

        layout_content = QWidget()
        typesetting_layout = QVBoxLayout(layout_content)
        typesetting_layout.addLayout(layout_form)
        typesetting_layout.addStretch()

        details_content = QWidget()
        details_layout = QVBoxLayout(details_content)
        details_layout.addLayout(details_form)
        details_layout.addWidget(self.delete_button)
        details_layout.addStretch()

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("Text block inspector")
        self.tabs.tabBar().setAccessibleName("Text block inspector tabs")
        self.text_tab = self._scroll_tab(text_content)
        self.layout_tab = self._scroll_tab(layout_content)
        self.details_tab = self._scroll_tab(details_content)
        self.tabs.addTab(self.text_tab, "Text")
        self.tabs.addTab(self.layout_tab, "Layout")
        self.tabs.addTab(self.details_tab, "Details")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tabs)

        for signal in (
            self.source_language_combo.currentIndexChanged,
            self.writing_mode_combo.currentIndexChanged,
            self.source_text_edit.textChanged,
            self.translated_text_edit.textChanged,
            self.alignment_combo.currentIndexChanged,
            self.font_style_combo.currentIndexChanged,
            self.width_spin.valueChanged,
            self.height_spin.valueChanged,
            self.stroke_width_spin.valueChanged,
            self.font_size_spin.valueChanged,
            self.rotation_spin.valueChanged,
            self.mirror_horizontal_check.toggled,
            self.mirror_vertical_check.toggled,
            self.line_spacing_spin.valueChanged,
            self.speaker_edit.textChanged,
            self.note_edit.textChanged,
            self.status_combo.currentIndexChanged,
        ):
            signal.connect(self._emit_block_changed)
        self.fill_color_edit.editingFinished.connect(
            lambda: self._finish_color_edit(self.fill_color_edit, "typesetting_fill_color")
        )
        self.stroke_color_edit.editingFinished.connect(
            lambda: self._finish_color_edit(self.stroke_color_edit, "typesetting_stroke_color")
        )
        self.font_family_combo.currentIndexChanged.connect(self._font_family_index_changed)
        self.font_family_combo.lineEdit().editingFinished.connect(self._finish_font_family_edit)
        self.status_combo.currentIndexChanged.connect(self._update_confirmation_buttons)
        self.confirm_ocr_button.clicked.connect(
            lambda: self._confirm_status(BlockStatus.OCR_REVIEWED)
        )
        self.confirm_translation_button.clicked.connect(
            lambda: self._confirm_status(BlockStatus.TRANSLATION_REVIEWED)
        )
        self.ocr_button.clicked.connect(self._emit_ocr_requested)
        self.translate_button.clicked.connect(self._emit_translate_requested)
        self.delete_button.clicked.connect(self._emit_delete_requested)
        self._update_color_swatch(self.fill_color_button, self.fill_color_edit)
        self._update_color_swatch(self.stroke_color_button, self.stroke_color_edit)
        self.set_block(None)

    def set_image_bounds(self, width: float | None, height: float | None) -> None:
        self._image_width = width
        self._image_height = height
        if self._block is not None:
            with QSignalBlocker(self.width_spin), QSignalBlocker(self.height_spin):
                self._set_bbox_limits(self._block)

    def _set_bbox_limits(self, block: TextBlock) -> tuple[float, float]:
        max_width = (
            max(MIN_BLOCK_SIZE, self._image_width - block.bbox.x)
            if self._image_width is not None
            else 1_000_000
        )
        max_height = (
            max(MIN_BLOCK_SIZE, self._image_height - block.bbox.y)
            if self._image_height is not None
            else 1_000_000
        )
        self.width_spin.setRange(MIN_BLOCK_SIZE, max_width)
        self.height_spin.setRange(MIN_BLOCK_SIZE, max_height)
        return max_width, max_height

    def set_block(self, block: TextBlock | None) -> None:
        controls = (
            self.source_language_combo,
            self.writing_mode_combo,
            self.source_text_edit,
            self.translated_text_edit,
            self.alignment_combo,
            self.font_family_combo,
            self.font_style_combo,
            self.fill_color_edit,
            self.stroke_color_edit,
            self.stroke_width_spin,
            self.width_spin,
            self.height_spin,
            self.font_size_spin,
            self.rotation_spin,
            self.mirror_horizontal_check,
            self.mirror_vertical_check,
            self.line_spacing_spin,
            self.speaker_edit,
            self.note_edit,
            self.status_combo,
            self.ocr_provider_edit,
        )
        blockers = [QSignalBlocker(control) for control in controls]
        self._block = block
        was_clamped = False

        if block is None:
            self.source_language_combo.setCurrentIndex(0)
            self.writing_mode_combo.setCurrentIndex(0)
            self.source_text_edit.clear()
            self.translated_text_edit.clear()
            self._set_combo_data(self.alignment_combo, TextAlignment.CENTER)
            self.width_spin.setRange(MIN_BLOCK_SIZE, 1_000_000)
            self.height_spin.setRange(MIN_BLOCK_SIZE, 1_000_000)
            self.width_spin.setValue(MIN_BLOCK_SIZE)
            self.height_spin.setValue(MIN_BLOCK_SIZE)
            self.font_family_combo.setCurrentIndex(0)
            self._populate_font_styles(None)
            self.fill_color_edit.clear()
            self.stroke_color_edit.clear()
            self.stroke_width_spin.setValue(0)
            self.font_size_spin.setValue(0)
            self.rotation_spin.setValue(0.0)
            self.mirror_horizontal_check.setChecked(False)
            self.mirror_vertical_check.setChecked(False)
            self.line_spacing_spin.setValue(-1)
            self.speaker_edit.clear()
            self.note_edit.clear()
            self.status_combo.setCurrentIndex(0)
            self.ocr_provider_edit.clear()
        else:
            self._set_combo_data(self.source_language_combo, block.source_language)
            self._set_combo_data(self.writing_mode_combo, block.writing_mode)
            self.source_text_edit.setPlainText(block.source_text)
            self.translated_text_edit.setPlainText(block.translated_text)
            self._set_combo_data(self.alignment_combo, block.typesetting_alignment)
            max_width, max_height = self._set_bbox_limits(block)
            was_clamped = not (
                MIN_BLOCK_SIZE <= block.bbox.width <= max_width
                and MIN_BLOCK_SIZE <= block.bbox.height <= max_height
            )
            self.width_spin.setValue(block.bbox.width)
            self.height_spin.setValue(block.bbox.height)
            bounded_bbox = block.bbox.model_copy(
                update={
                    "width": min(max(block.bbox.width, MIN_BLOCK_SIZE), max_width),
                    "height": min(max(block.bbox.height, MIN_BLOCK_SIZE), max_height),
                }
            )
            if was_clamped:
                self._block = block.model_copy(update={"bbox": bounded_bbox})
                block = self._block
            self._set_font_family_data(block.typesetting_font_family)
            self._populate_font_styles(block.typesetting_font_family, block.typesetting_font_style)
            self.fill_color_edit.setText(block.typesetting_fill_color or "")
            self.stroke_color_edit.setText(block.typesetting_stroke_color or "")
            self.stroke_width_spin.setValue(block.typesetting_stroke_width or 0)
            self.font_size_spin.setValue(block.typesetting_font_size or 0)
            self.rotation_spin.setValue(block.rotation_degrees)
            self.mirror_horizontal_check.setChecked(block.mirror_horizontal)
            self.mirror_vertical_check.setChecked(block.mirror_vertical)
            self.line_spacing_spin.setValue(
                block.typesetting_line_spacing if block.typesetting_line_spacing is not None else -1
            )
            self.speaker_edit.setText(block.speaker)
            self.note_edit.setPlainText(block.note)
            self._set_combo_data(self.status_combo, block.status)
            self.ocr_provider_edit.setText(block.ocr_provider or "")

        del blockers
        self._update_color_swatch(self.fill_color_button, self.fill_color_edit)
        self._update_color_swatch(self.stroke_color_button, self.stroke_color_edit)
        self._clear_render_metric()
        self.setEnabled(block is not None)
        self._update_confirmation_buttons()
        self._update_tab_indicators()
        if was_clamped:
            self.block_changed.emit(self._block)

    def _populate_font_styles(self, family: str | None, selected: str | None = None) -> None:
        with QSignalBlocker(self.font_style_combo):
            self.font_style_combo.clear()
            self.font_style_combo.addItem(_AUTO, None)
            if family:
                for style in font_styles(family):
                    self.font_style_combo.addItem(style, style)
            if selected is not None and self.font_style_combo.findData(selected) < 0:
                index = self.font_style_combo.count()
                self.font_style_combo.addItem(f"{selected} (not available locally)", selected)
                self.font_style_combo.setItemData(
                    index,
                    "The saved style is not available locally; choose an installed style or Auto.",
                    Qt.ItemDataRole.ToolTipRole,
                )
            self._set_combo_data(self.font_style_combo, selected)

    def _set_font_family_data(self, value: str | None) -> None:
        if value is None:
            self.font_family_combo.setCurrentIndex(0)
            return
        index = self.font_family_combo.findData(value)
        if index < 0:
            index = self.font_family_combo.count()
            self.font_family_combo.addItem(f"{value} (not available locally)", value)
            self.font_family_combo.setItemData(
                index,
                "The saved font family is not available locally; choose an installed font or Auto.",
                Qt.ItemDataRole.ToolTipRole,
            )
        self.font_family_combo.setCurrentIndex(index)

    def _font_family_index_changed(self, index: int) -> None:
        if self._block is None or index < 0:
            return
        value = self._combo_value(self.font_family_combo)
        self._populate_font_styles(value)
        self._emit_block_changed()

    def _finish_font_family_edit(self) -> None:
        if self._block is None:
            return
        if self.font_family_combo.currentText().strip().casefold() in _AUTO_ALIASES:
            self.font_family_combo.setCurrentIndex(0)
            return
        index = self.font_family_combo.currentIndex()
        expected = self.font_family_combo.itemText(index) if index >= 0 else _AUTO
        if self.font_family_combo.currentText() == expected:
            return
        with QSignalBlocker(self.font_family_combo):
            self.font_family_combo.setCurrentIndex(index if index >= 0 else 0)

    @staticmethod
    def _combo_value(combo: QComboBox) -> str | None:
        if combo.currentIndex() < 0:
            return None
        value = combo.itemData(combo.currentIndex())
        if isinstance(value, str):
            return value
        return None

    @staticmethod
    def _runtime_field(accessible_name: str, tool_tip: str) -> QLineEdit:
        field = QLineEdit()
        field.setReadOnly(True)
        field.setAccessibleName(accessible_name)
        field.setToolTip(tool_tip)
        return field

    @staticmethod
    def _scroll_tab(content: QWidget) -> QScrollArea:
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setWidget(content)
        return scroll_area

    def _color_button(self, field: QLineEdit, attribute: str, title: str) -> QToolButton:
        button = QToolButton()
        button.setAutoRaise(False)
        button.setMinimumSize(76, 36)
        button.setMaximumWidth(96)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setAccessibleName(title)
        button.setToolTip(title)
        button.clicked.connect(lambda _checked=False: self._choose_color(field, attribute, title))
        return button

    def _update_color_swatch(self, button: QToolButton, field: QLineEdit) -> None:
        value = self._text_override(field)
        if value is None:
            button.setText(_AUTO)
            button.setStyleSheet(
                "QToolButton { background-color: #eeeeee; color: #202020; "
                "border: 1px solid #777777; border-radius: 3px; padding: 4px; }"
            )
            button.setAccessibleDescription("Automatic color selection")
            return
        color = QColor(value)
        foreground = "#000000" if color.lightnessF() > 0.55 else "#FFFFFF"
        button.setText(value.upper())
        button.setStyleSheet(
            f"QToolButton {{ background-color: {value}; color: {foreground}; "
            "border: 1px solid #555555; border-radius: 3px; padding: 4px; }"
        )
        button.setAccessibleDescription(f"Current color {value.upper()}")

    def _choose_color(self, field: QLineEdit, attribute: str, title: str) -> None:
        if self._block is None:
            return
        current = self._text_override(field) or getattr(self._block, attribute) or "#000000"
        selected = QColorDialog.getColor(QColor(current), self, title)
        if not selected.isValid():
            return
        field.setText(selected.name(QColor.NameFormat.HexRgb).upper())
        self._finish_color_edit(field, attribute)

    def _finish_color_edit(self, field: QLineEdit, attribute: str) -> None:
        if self._block is None:
            return
        value = field.text().strip()
        if value.casefold() in _AUTO_ALIASES:
            field.clear()
            self._update_color_swatch(
                self.fill_color_button
                if attribute == "typesetting_fill_color"
                else self.stroke_color_button,
                field,
            )
            self._emit_block_changed()
            return
        if value and (
            not value.startswith("#") or len(value) != 7 or not field.hasAcceptableInput()
        ):
            field.setText(getattr(self._block, attribute) or "")
            self._update_color_swatch(
                self.fill_color_button
                if attribute == "typesetting_fill_color"
                else self.stroke_color_button,
                field,
            )
            return
        field.setText(value.upper() if value else "")
        self._update_color_swatch(
            self.fill_color_button
            if attribute == "typesetting_fill_color"
            else self.stroke_color_button,
            field,
        )
        self._emit_block_changed()

    def set_thai_font_label(self, label: str | os.PathLike[str] | None) -> None:
        self.thai_font_edit.setText(str(label) if label is not None else _AUTO)

    def set_render_metric(self, metric: RenderMetric | None) -> None:
        if metric is None:
            self._clear_render_metric()
            return
        if self._block is None or metric.block_id != self._block.id:
            return
        self.effective_font_edit.setText(metric.font_label or _AUTO)
        self.effective_font_size_edit.setText(f"{metric.font_size_px} px")
        self.effective_fill_edit.setText(self._format_rgb(metric.fill_rgb))
        self.effective_stroke_edit.setText(
            self._format_rgb(metric.stroke_rgb) if metric.stroke_rgb is not None else "None"
        )
        self.effective_stroke_width_edit.setText(f"{metric.stroke_width_px} px")

    def _clear_render_metric(self) -> None:
        self.effective_font_edit.setText(_AUTO)
        self.effective_font_size_edit.setText(_AUTO)
        self.effective_fill_edit.setText(_AUTO)
        self.effective_stroke_edit.setText(_AUTO)
        self.effective_stroke_width_edit.setText(_AUTO)

    @staticmethod
    def _format_rgb(color: tuple[int, int, int]) -> str:
        return "#{:02X}{:02X}{:02X}".format(*color)

    @staticmethod
    def _text_override(field: QLineEdit) -> str | None:
        value = field.text().strip()
        return value if value and value.casefold() not in _AUTO_ALIASES else None

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value.value if hasattr(value, "value") else value)
        combo.setCurrentIndex(index)

    def _emit_block_changed(self, *_args: object) -> None:
        if self._block is None:
            return
        update = {
            "source_language": (
                SourceLanguage(value)
                if (value := self.source_language_combo.currentData()) is not None
                else None
            ),
            "writing_mode": WritingMode(self.writing_mode_combo.currentData()),
            "source_text": self.source_text_edit.toPlainText(),
            "translated_text": self.translated_text_edit.toPlainText(),
            "bbox": BoundingBox(
                x=self._block.bbox.x,
                y=self._block.bbox.y,
                width=self.width_spin.value(),
                height=self.height_spin.value(),
            ),
            "typesetting_alignment": TextAlignment(self.alignment_combo.currentData()),
            "typesetting_font_family": self._combo_value(self.font_family_combo),
            "typesetting_font_style": self._combo_value(self.font_style_combo),
            "typesetting_fill_color": self._text_override(self.fill_color_edit),
            "typesetting_stroke_color": self._text_override(self.stroke_color_edit),
            "typesetting_stroke_width": self.stroke_width_spin.value(),
            "typesetting_font_size": self.font_size_spin.value() or None,
            "rotation_degrees": self.rotation_spin.value(),
            "mirror_horizontal": self.mirror_horizontal_check.isChecked(),
            "mirror_vertical": self.mirror_vertical_check.isChecked(),
            "typesetting_line_spacing": self.line_spacing_spin.value()
            if self.line_spacing_spin.value() >= 0
            else None,
            "speaker": self.speaker_edit.text(),
            "note": self.note_edit.toPlainText(),
            "status": BlockStatus(self.status_combo.currentData()),
            "updated_at": utc_now(),
        }
        try:
            self._block = TextBlock.model_validate(
                self._block.model_dump(mode="python") | update,
            )
        except ValidationError:
            return
        self.block_changed.emit(self._block)
        self._update_tab_indicators()

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

    def _emit_ocr_requested(self) -> None:
        if self._block is not None:
            self.ocr_requested.emit(self._block.id)

    def _emit_translate_requested(self) -> None:
        if self._block is not None:
            self.translate_requested.emit(self._block.id)

    def _update_tab_indicators(self) -> None:
        self.tabs.setTabText(0, "Text")
        self.tabs.setTabText(1, "Layout")
        self.tabs.setTabText(2, "Details")
        if self._block is None:
            return
        if self._block.status is BlockStatus.LANGUAGE_REVIEW_REQUIRED:
            self.tabs.setTabText(0, "Text ⚠")
        elif self._block.status is BlockStatus.ERROR:
            self.tabs.setTabText(2, "Details ⚠")
