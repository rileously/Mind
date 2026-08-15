from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPalette, QPixmap


PALETTES = {
    "dark": {
        "window": "#1F1F1F",
        "sidebar": "#202020",
        "surface": "#282828",
        "surface_alt": "#2D2D2D",
        "surface_hover": "#323232",
        "border": "#3D3D3D",
        "border_soft": "#333333",
        "text": "#F3F3F3",
        "muted": "#9B9B9B",
        "accent": "#00B7C3",
        "accent_hover": "#29CAD3",
        "accent_soft": "#153A3D",
        "danger": "#FF6F86",
        "warning": "#F7B955",
    },
    "light": {
        "window": "#F3F6FA",
        "sidebar": "#F8FAFD",
        "surface": "#FFFFFF",
        "surface_alt": "#EDF2F8",
        "surface_hover": "#E5ECF5",
        "border": "#D6E0EC",
        "border_soft": "#E6ECF3",
        "text": "#111C2F",
        "muted": "#63748B",
        "accent": "#0D9F96",
        "accent_hover": "#087E78",
        "accent_soft": "#DDF5F2",
        "danger": "#D9475A",
        "warning": "#B87800",
    },
}


ACCENTS = {
    "teal": {
        "dark": ("#00B7C3", "#29CAD3", "#153A3D"),
        "light": ("#0D9F96", "#087E78", "#DDF5F2"),
    },
    "blue": {
        "dark": ("#4C9DFF", "#70B1FF", "#172F52"),
        "light": ("#2879D8", "#1E63B3", "#DFEDFF"),
    },
    "purple": {
        "dark": ("#A985FF", "#BCA2FF", "#302451"),
        "light": ("#7454C7", "#5F42AD", "#EEE8FF"),
    },
    "rose": {
        "dark": ("#FF7FA4", "#FF9AB8", "#4A2333"),
        "light": ("#CC416D", "#AD3159", "#FFE5ED"),
    },
    "orange": {
        "dark": ("#F6A84A", "#F9BB70", "#442F19"),
        "light": ("#C87311", "#A85C08", "#FFF0DC"),
    },
}


def resolved_theme(choice: str) -> str:
    if choice in {"dark", "light"}:
        return choice
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    hints = QApplication.styleHints()
    return "dark" if hints.colorScheme() == Qt.ColorScheme.Dark else "light"


def theme_palette(choice: str, accent: str = "teal") -> dict[str, str]:
    mode = resolved_theme(choice)
    palette = dict(PALETTES[mode])
    accent_name = accent if accent in ACCENTS else "teal"
    palette["accent"], palette["accent_hover"], palette["accent_soft"] = ACCENTS[accent_name][mode]
    return palette


def qt_palette(choice: str, accent: str = "teal") -> QPalette:
    """Palette roles used by custom-painted Fluent controls."""
    colors = theme_palette(choice, accent)
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(colors["window"]))
    palette.setColor(QPalette.WindowText, QColor(colors["text"]))
    palette.setColor(QPalette.Base, QColor(colors["surface"]))
    palette.setColor(QPalette.AlternateBase, QColor(colors["surface_alt"]))
    palette.setColor(QPalette.Text, QColor(colors["text"]))
    palette.setColor(QPalette.Button, QColor(colors["surface_alt"]))
    palette.setColor(QPalette.ButtonText, QColor(colors["text"]))
    palette.setColor(QPalette.Mid, QColor(colors["border"]))
    palette.setColor(QPalette.PlaceholderText, QColor(colors["muted"]))
    palette.setColor(QPalette.Highlight, QColor(colors["accent"]))
    palette.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    return palette


def stylesheet(choice: str, accent: str = "teal") -> str:
    p = theme_palette(choice, accent)
    return f"""
    * {{
        font-family: "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI";
        font-size: 13px;
        color: {p['text']};
    }}
    QMainWindow, QDialog, QWidget#AppRoot, QWidget#ContentRoot, QWidget#Page, QStackedWidget,
    QScrollArea, QScrollArea QWidget#qt_scrollarea_viewport {{ background: {p['window']}; }}
    QDialog#PaletteDialog, QDialog#DefinitionPopup, QDialog#AskAiPopup, QDialog#QuickPastePopup, QDialog#SnipCard, QDialog#ClipboardHistoryDialog, QDialog#SecretShieldCard, QDialog#UrlPeekCard, QDialog#GhostTextOverlay {{ background: transparent; }}
    QWidget#Sidebar {{ background: {p['sidebar']}; border-right: 1px solid {p['border_soft']}; }}
    QLabel#LogoMark {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 {p['accent_hover']}, stop:1 {p['accent']});
        color: white; border-radius: 14px; font-size: 21px; font-weight: 800;
    }}
    QLabel#BrandLogo {{ background: transparent; border: 0; }}
    QLabel#Brand {{ font-size: 22px; font-weight: 750; }}
    QLabel#BrandCaption {{ color: {p['muted']}; font-size: 10px; font-weight: 600; }}
    QLabel#PageEyebrow, QLabel#CardEyebrow {{
        color: {p['accent']}; font-size: 10px; font-weight: 750; letter-spacing: 1.5px;
    }}
    QLabel#PageTitle {{
        font-family: "Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI";
        font-size: 26px; font-weight: 650;
        color: {p['text']};
    }}
    QLabel#PageSubtitle {{ color: {p['muted']}; font-size: 12px; }}
    QLabel#CardTitle {{ font-size: 16px; font-weight: 700; color: {p['text']}; }}
    QLabel#CardBody {{ color: {p['muted']}; font-size: 12px; }}
    QLabel#HeroHeading {{
        font-family: "Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI";
        font-size: 32px; font-weight: 760;
        color: {p['text']};
    }}
    QLabel#HeroSubheading {{
        color: {p['muted']}; font-size: 14px; max-width: 720px;
    }}
    QFrame#Card {{
        background: {p['surface']}; border: 1px solid {p['border_soft']}; border-radius: 14px;
    }}
    QFrame#Card:hover {{ border-color: {p['border']}; }}
    QFrame#StatusCard {{
        background: {p['surface_alt']}; border: 1px solid {p['border_soft']}; border-radius: 12px;
    }}
    QFrame#SettingRow {{
        background: {p['surface_alt']}; border: 1px solid {p['border_soft']}; border-radius: 10px;
    }}
    QFrame#SettingRow:hover {{
        background: {p['surface_hover']}; border-color: {p['border']};
    }}
    QWidget#PaletteShell {{
        background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 18px;
    }}
    QWidget#DefinitionCard, QWidget#AskAiCard {{
        background: {p['surface']}; border: 1px solid {p['accent']}; border-radius: 13px;
    }}
    QWidget#QuickPasteCard {{
        background: {p['surface']}; border: 1px solid {p['accent']}; border-radius: 12px;
    }}
    QLabel#QuickPasteIcon {{ font-size: 13px; }}
    QLabel#QuickPastePreview {{
        color: {p['text']}; font-size: 12px; font-weight: 550;
    }}
    QPushButton#QuickPasteButton {{
        min-height: 26px; padding: 0 12px; font-size: 11px; font-weight: 700;
        border-radius: 6px; background: {p['accent']}; color: white; border: 0;
    }}
    QPushButton#QuickPasteButton:hover {{ background: {p['accent_hover']}; }}
    QWidget#SnipCardFrame {{
        background: {p['surface']}; border: 1px solid {p['accent']}; border-radius: 14px;
    }}
    QLabel#SnipHeaderIcon {{ font-size: 15px; }}
    QLabel#SnipHeaderTitle {{ font-size: 14px; font-weight: 700; color: {p['text']}; }}
    QLabel#SnipSizeBadge {{
        font-size: 10px; font-weight: 600; color: {p['muted']};
        background: {p['surface_alt']}; border: 1px solid {p['border']};
        border-radius: 4px; padding: 2px 6px;
    }}
    QLabel#SnipThumbnail {{
        background: {p['surface_alt']}; border: 1px solid {p['border_soft']};
        border-radius: 8px; padding: 4px;
    }}
    QFrame#SnipResultContainer {{
        background: {p['surface_alt']}; border: 1px solid {p['border']}; border-radius: 8px;
    }}
    QLabel#SnipResultText {{
        color: {p['text']}; font-size: 12px;
    }}
    QPushButton#SnipActionPrimary {{
        min-height: 28px; padding: 0 10px; font-size: 11px; font-weight: 650;
        border-radius: 6px; background: {p['accent']}; color: white; border: 0;
    }}
    QPushButton#SnipActionPrimary:hover {{ background: {p['accent_hover']}; }}
    QPushButton#SnipActionSecondary {{
        min-height: 28px; padding: 0 8px; font-size: 11px; font-weight: 600;
        border-radius: 6px; background: {p['surface_alt']}; color: {p['text']};
        border: 1px solid {p['border']};
    }}
    QPushButton#SnipActionSecondary:hover {{
        background: {p['surface_hover']}; border-color: {p['accent']};
    }}
    QWidget#ClipboardHistoryShell {{
        background: {p['surface']}; border: 1px solid {p['accent']}; border-radius: 14px;
    }}
    QLabel#ClipboardHeaderIcon {{ font-size: 16px; }}
    QLabel#ClipboardHeaderTitle {{ font-size: 14px; font-weight: 700; color: {p['text']}; }}
    QPushButton#ClipboardClearButton {{
        background: transparent; color: {p['muted']}; border: 0; font-size: 11px; font-weight: 600; padding: 2px 6px;
    }}
    QPushButton#ClipboardClearButton:hover {{ color: {p['danger']}; }}
    QLineEdit#ClipboardSearchInput {{
        background: {p['surface_alt']}; color: {p['text']}; border: 1px solid {p['border']};
        border-radius: 8px; padding: 6px 10px; font-size: 12px;
    }}
    QLineEdit#ClipboardSearchInput:focus {{
        border-color: {p['accent']};
    }}
    QPushButton#ClipboardCategoryFilterTab {{
        background: {p['surface_alt']}; color: {p['muted']}; border: 1px solid {p['border_soft']};
        border-radius: 6px; padding: 3px 8px; font-size: 11px; font-weight: 600;
    }}
    QPushButton#ClipboardCategoryFilterTab:hover {{
        background: {p['surface_hover']}; color: {p['text']};
    }}
    QPushButton#ClipboardCategoryFilterTab:checked {{
        background: {p['accent']}; color: white; border-color: {p['accent']};
    }}
    QScrollArea#ClipboardHistoryScroll, QScrollArea#ClipboardHistoryScroll QWidget {{
        background: transparent; border: 0;
    }}
    QFrame#ClipboardHistoryItem {{
        background: {p['surface_alt']}; border: 1px solid {p['border_soft']}; border-radius: 8px;
    }}
    QFrame#ClipboardHistoryItem:hover {{
        background: {p['surface_hover']}; border-color: {p['border']};
    }}
    QFrame#ClipboardHistoryItem[selected="true"] {{
        background: {p['surface_hover']}; border-color: {p['accent']};
    }}
    QLabel#ClipboardCategoryBadge {{
        font-size: 10px; font-weight: 700; color: {p['accent']};
        background: {p['surface']}; border-radius: 4px; padding: 1px 5px;
    }}
    QLabel#ClipboardTimeLabel, QLabel#ClipboardCountLabel {{
        font-size: 10px; color: {p['muted']};
    }}
    QPushButton#ClipboardItemActionPin, QPushButton#ClipboardItemActionAi, QPushButton#ClipboardItemActionDelete {{
        background: transparent; color: {p['muted']}; border: 0; font-size: 11px; padding: 2px 4px;
        border-radius: 4px;
    }}
    QPushButton#ClipboardItemActionPin:hover, QPushButton#ClipboardItemActionAi:hover, QPushButton#ClipboardItemActionDelete:hover {{
        background: {p['surface']}; color: {p['text']};
    }}
    QLabel#ClipboardItemPreview {{
        font-size: 12px; color: {p['text']}; line-height: 1.3;
    }}
    QLabel#ClipboardEmptyLabel {{
        font-size: 12px; color: {p['muted']}; padding: 30px 0;
    }}
    QLabel#ClipboardFooterHint {{
        font-size: 10px; color: {p['muted']}; padding-top: 2px;
    }}
    QWidget#SecretShieldFrame {{
        background: {p['surface']}; border: 1px solid #E6A23C; border-radius: 14px;
    }}
    QLabel#SecretShieldIcon {{ font-size: 16px; }}
    QLabel#SecretShieldTitle {{ font-size: 13px; font-weight: 700; color: {p['text']}; }}
    QLabel#SecretShieldTypeBadge {{
        font-size: 10px; font-weight: 700; color: #E6A23C;
        background: {p['surface_alt']}; border: 1px solid #E6A23C;
        border-radius: 4px; padding: 2px 6px;
    }}
    QFrame#SecretShieldPreviewContainer {{
        background: {p['surface_alt']}; border: 1px solid {p['border']}; border-radius: 6px;
    }}
    QLabel#SecretShieldPreviewText {{
        font-family: "Cascadia Mono", monospace; font-size: 11px; color: {p['text']};
    }}
    QPushButton#SecretShieldRedactButton {{
        min-height: 28px; padding: 0 12px; font-size: 11px; font-weight: 700;
        border-radius: 6px; background: #E6A23C; color: black; border: 0;
    }}
    QPushButton#SecretShieldRedactButton:hover {{ background: #F5BC68; }}
    QPushButton#SecretShieldKeepButton {{
        min-height: 28px; padding: 0 10px; font-size: 11px; font-weight: 600;
        border-radius: 6px; background: {p['surface_alt']}; color: {p['text']};
        border: 1px solid {p['border']};
    }}
    QPushButton#SecretShieldKeepButton:hover {{
        background: {p['surface_hover']};
    }}
    QWidget#UrlPeekFrame {{
        background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 14px;
    }}
    QLabel#UrlPeekDomainBadge {{
        font-size: 11px; font-weight: 700; color: {p['accent']};
        background: {p['surface_alt']}; border: 1px solid {p['border']};
        border-radius: 4px; padding: 2px 6px;
    }}
    QLabel#UrlPeekTitle {{
        font-size: 13px; font-weight: 700; color: {p['text']};
    }}
    QLabel#UrlPeekUrlText {{
        font-family: "Cascadia Mono", monospace; font-size: 11px; color: {p['muted']};
    }}
    QPushButton#UrlPeekCleanButton {{
        min-height: 28px; padding: 0 10px; font-size: 11px; font-weight: 700;
        border-radius: 6px; background: {p['accent']}; color: white; border: 0;
    }}
    QPushButton#UrlPeekCleanButton:hover {{ background: {p['accent_hover']}; }}
    QPushButton#UrlPeekAiButton, QPushButton#UrlPeekOpenButton {{
        min-height: 28px; padding: 0 10px; font-size: 11px; font-weight: 600;
        border-radius: 6px; background: {p['surface_alt']}; color: {p['text']};
        border: 1px solid {p['border']};
    }}
    QPushButton#UrlPeekAiButton:hover, QPushButton#UrlPeekOpenButton:hover {{
        background: {p['surface_hover']};
    }}
    QFrame#GhostTextFrame {{
        background: {p['surface']}; border: 1px solid {p['accent']}; border-radius: 12px;
    }}
    QLabel#GhostTextIcon {{ font-size: 13px; }}
    QLabel#GhostTextLabel {{
        font-size: 12px; font-style: italic; color: {p['text']}; font-weight: 500;
    }}
    QLabel#GhostTextTabBadge {{
        font-size: 10px; font-weight: 700; color: {p['accent']};
        background: {p['surface_alt']}; border: 1px solid {p['border']};
        border-radius: 4px; padding: 2px 5px;
    }}
    QLabel#SectionTitle {{ font-size: 15px; font-weight: 650; }}
    QLabel#SettingTitle {{ font-size: 13px; font-weight: 600; }}
    QLabel#SettingsGroupTitle {{
        color: {p['muted']}; font-size: 11px; font-weight: 700; letter-spacing: 1.1px;
        padding: 2px 4px 3px 4px;
    }}
    QLabel#SettingIcon {{
        background: {p['surface_alt']}; color: {p['accent']}; border: 1px solid {p['border']};
        border-radius: 6px; font-family: "Segoe UI Symbol", "Segoe UI"; font-size: 14px;
        font-weight: 650;
    }}
    QLabel#MonoValue {{
        color: {p['text']}; font-family: "Cascadia Mono", "Consolas"; font-size: 12px;
    }}
    QLabel#HeroTitle {{
        font-family: "Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI";
        font-size: 29px; font-weight: 760;
    }}
    QLabel#StatValue {{ font-size: 21px; font-weight: 730; }}
    QLabel#Muted, QLabel[muted="true"] {{ color: {p['muted']}; }}
    QLabel#Accent {{ color: {p['accent']}; font-weight: 650; }}
    QLabel#HeroPill, QLabel#StatusPill, QLabel#SoftBadge {{
        color: {p['accent']}; background: {p['accent_soft']}; border: 1px solid {p['accent']};
        border-radius: 10px; padding: 4px 9px; font-size: 10px; font-weight: 700;
    }}
    QLabel#StatIcon {{
        background: {p['accent_soft']}; color: {p['accent']}; border: 1px solid {p['border']};
        border-radius: 11px; font-size: 17px; font-weight: 750;
    }}
    QLabel#CodeBlock {{
        font-family: "Cascadia Code", "Cascadia Mono", "Consolas"; font-size: 15px;
        color: {p['text']}; background: {p['surface_alt']}; border: 1px solid {p['border']};
        border-radius: 12px; padding: 14px 16px;
    }}
    QLabel#Kbd {{
        font-family: "Cascadia Code", "Consolas"; font-size: 11px; font-weight: 650;
        background: {p['surface_alt']}; border: 1px solid {p['border']}; border-radius: 7px;
        padding: 4px 7px;
    }}
    QFrame#Card, QWidget#Card, QFrame#StatCard, QFrame#StatusCard {{
        background: {p['surface']}; border: 1px solid {p['border_soft']}; border-radius: 9px;
    }}
    QFrame#AccentCard, QFrame#HeroCard {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
            stop:0 {p['accent_soft']}, stop:0.72 {p['surface']}, stop:1 {p['surface_alt']});
        border: 1px solid {p['accent']}; border-radius: 10px;
    }}
    QFrame#InsetCard {{
        background: {p['surface_alt']}; border: 1px solid {p['border_soft']}; border-radius: 8px;
    }}
    QFrame#SettingRow {{
        background: {p['surface']}; border: 1px solid {p['border_soft']}; border-radius: 8px;
    }}
    QFrame#SettingRow:hover {{
        background: {p['surface_hover']}; border-color: {p['border']};
    }}
    QWidget#PaletteShell {{
        background: {p['surface']}; border: 1px solid {p['border']}; border-radius: 18px;
    }}
    QWidget#DefinitionCard, QWidget#AskAiCard {{
        background: {p['surface']}; border: 1px solid {p['accent']}; border-radius: 13px;
    }}
    QPushButton#AskAiPillButton {{
        min-height: 32px; padding: 0 16px; border-radius: 16px;
        background: {p['surface']}; color: {p['accent']}; border: 1.5px solid {p['accent']};
        font-size: 12px; font-weight: 750;
    }}
    QPushButton#AskAiPillButton:hover {{
        background: {p['accent']}; color: white; border-color: {p['accent_hover']};
    }}
    QLabel#AskAiCardTitle {{
        font-size: 14px; font-weight: 750; color: {p['accent']};
    }}
    QLabel#AskAiCardIcon {{
        font-size: 14px; color: {p['accent']}; font-weight: 800;
    }}
    QLabel#AskAiQuestion {{
        color: {p['muted']}; font-size: 12px; font-style: italic;
    }}
    QLabel#AskAiAnswer {{
        color: {p['text']}; font-size: 13px;
    }}
    QPushButton#AskAiCopyButton, QPushButton#AskAiActionBtn {{
        min-height: 28px; padding: 0 10px; font-size: 12px; border-radius: 6px;
        background: {p['accent_soft']}; color: {p['accent']}; border: 1px solid {p['accent']};
        font-weight: 650;
    }}
    QPushButton#AskAiCopyButton:hover, QPushButton#AskAiActionBtn:hover {{
        background: {p['accent']}; color: white;
    }}
    QLabel#DefinitionWord {{
        font-family: "Segoe UI Variable Display", "Segoe UI Variable", "Segoe UI";
        font-size: 20px; font-weight: 760; color: {p['text']};
    }}
    QLabel#DefinitionPronunciation {{
        color: {p['muted']}; font-size: 12px;
    }}
    QLabel#DefinitionPart {{
        color: {p['accent']}; font-size: 10px; font-weight: 750;
    }}
    QLabel#DefinitionBody {{
        color: {p['text']}; font-size: 13px;
    }}
    QLabel#PalettePreview {{
        background: {p['surface_alt']}; color: {p['muted']}; border: 1px solid {p['border_soft']};
        border-radius: 11px; padding: 11px;
    }}
    QLabel#PaletteImagePreview {{
        background: {p['surface_alt']}; border: 1px solid {p['border']}; border-radius: 10px;
        padding: 4px;
    }}
    QLabel#PalettePreview[thaana="true"] {{
        font-family: "Faruma", "MV Boli", "Nirmala UI"; font-size: 18px;
    }}
    QPushButton {{
        min-height: 34px; padding: 0 13px; border-radius: 6px;
        background: {p['surface_alt']}; border: 1px solid {p['border']}; font-weight: 600;
    }}
    QPushButton:hover {{ background: {p['surface_hover']}; border-color: {p['accent']}; }}
    QPushButton:pressed {{ background: {p['accent_soft']}; }}
    QPushButton:disabled {{ color: {p['muted']}; background: {p['surface']}; border-color: {p['border_soft']}; }}
    QPushButton[primary="true"] {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {p['accent']}, stop:1 {p['accent_hover']});
        border-color: {p['accent']}; color: white; padding: 0 20px;
    }}
    QPushButton[primary="true"]:hover {{ border-color: {p['accent_hover']}; }}
    QPushButton[danger="true"] {{ color: {p['danger']}; }}
    QPushButton#NavButton {{
        text-align: left; padding-left: 13px; border: 0; border-left: 3px solid transparent;
        background: transparent; color: {p['muted']}; min-height: 40px; border-radius: 6px;
    }}
    QPushButton#NavButton:hover {{ background: {p['surface_alt']}; color: {p['text']}; }}
    QPushButton#NavButton:checked {{
        background: {p['surface_alt']}; color: {p['text']}; border-left-color: {p['accent']};
        font-weight: 700;
    }}
    QPushButton#EngineAction {{ min-height: 28px; padding: 0 10px; font-size: 11px; border-radius: 5px; }}
    QWidget#SegmentedControl {{
        background: {p['window']}; border: 1px solid {p['border']}; border-radius: 6px;
    }}
    QPushButton#SegmentButton {{
        min-height: 28px; padding: 0 10px; margin: 0; border: 0;
        border-right: 1px solid {p['border']}; border-radius: 0;
        background: transparent; color: {p['muted']}; font-size: 12px; font-weight: 500;
    }}
    QPushButton#SegmentButton[segmentPosition="first"] {{
        border-top-left-radius: 4px; border-bottom-left-radius: 4px;
    }}
    QPushButton#SegmentButton[segmentPosition="last"] {{
        border-right: 0; border-top-right-radius: 4px; border-bottom-right-radius: 4px;
    }}
    QPushButton#SegmentButton:hover {{ background: {p['surface_hover']}; color: {p['text']}; border-color: {p['border']}; }}
    QPushButton#SegmentButton:checked {{ background: {p['surface_alt']}; color: {p['text']}; font-weight: 650; }}
    QPushButton#PaletteAction {{ text-align: left; min-height: 42px; padding-left: 14px; }}
    QPushButton#DefinitionClose {{
        min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
        padding: 0; border: 0; border-radius: 6px; background: transparent;
        color: {p['muted']}; font-size: 18px; font-weight: 500;
    }}
    QPushButton#DefinitionClose:hover {{ background: {p['surface_hover']}; color: {p['text']}; }}
    QPushButton#DefinitionSource, QPushButton#DefinitionGoogle {{
        min-height: 22px; padding: 0; border: 0; background: transparent;
        color: {p['muted']}; font-size: 10px; font-weight: 500;
    }}
    QPushButton#DefinitionSource:hover, QPushButton#DefinitionGoogle:hover {{ color: {p['accent']}; border: 0; background: transparent; }}
    QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QListWidget {{
        background: {p['surface_alt']}; border: 1px solid {p['border']};
        border-radius: 6px; padding: 7px 10px; selection-background-color: {p['accent']};
    }}
    QLineEdit#SidebarSearch {{ min-height: 32px; padding: 0 10px; font-size: 12px; }}
    QLineEdit#PrefixEdit {{
        color: {p['accent']}; font-family: "Cascadia Mono", "Consolas"; font-weight: 700;
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {p['accent']}; }}
    QComboBox::drop-down {{ border: 0; width: 28px; }}
    QFrame#ProviderSelectorShell {{
        background: transparent; border: 0; border-radius: 12px;
    }}
    QComboBox#ProviderSelector {{
        min-height: 46px; padding: 0 16px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
            stop:0 {p['accent_soft']}, stop:0.42 {p['surface_alt']});
        border: 2px solid {p['accent']}; border-right: 0;
        border-top-left-radius: 12px; border-bottom-left-radius: 12px;
        border-top-right-radius: 0; border-bottom-right-radius: 0;
        font-size: 14px; font-weight: 700;
    }}
    QComboBox#ProviderSelector:hover {{ background: {p['surface_hover']}; }}
    QComboBox#ProviderSelector:focus {{ border-color: {p['accent_hover']}; }}
    QComboBox#ProviderSelector::drop-down {{ width: 0; border: 0; }}
    QComboBox#ProviderSelector::down-arrow {{ image: none; width: 0; height: 0; }}
    QPushButton#ProviderDropdownButton {{
        min-height: 46px; padding: 0; margin: 0;
        background: {p['accent_soft']}; color: {p['accent']};
        border: 2px solid {p['accent']}; border-left: 1px solid {p['accent']};
        border-top-left-radius: 0; border-bottom-left-radius: 0;
        border-top-right-radius: 12px; border-bottom-right-radius: 12px;
        font-family: "Segoe UI Symbol", "Segoe UI"; font-size: 18px; font-weight: 800;
    }}
    QPushButton#ProviderDropdownButton:hover {{
        background: {p['accent']}; color: white; border-color: {p['accent_hover']};
    }}
    QPushButton#ProviderDropdownButton:pressed {{ background: {p['accent_hover']}; color: white; }}
    QComboBox QAbstractItemView {{
        background: {p['surface']}; border: 1px solid {p['border']};
        selection-background-color: {p['accent_soft']}; outline: 0; padding: 4px;
    }}
    QComboBox#ProviderSelector QAbstractItemView {{
        border: 2px solid {p['accent']}; border-radius: 10px; padding: 6px;
    }}
    QComboBox#ProviderSelector QAbstractItemView::item {{ min-height: 34px; padding: 4px 10px; }}
    QTableWidget {{
        background: {p['surface']}; alternate-background-color: {p['surface_alt']};
        border: 1px solid {p['border_soft']}; border-radius: 14px; gridline-color: {p['border_soft']};
    }}
    QHeaderView::section {{
        background: {p['surface_alt']}; border: 0; border-bottom: 1px solid {p['border']};
        padding: 11px; font-size: 11px; font-weight: 700; color: {p['muted']};
    }}
    QTableWidget::item {{ padding: 9px; border-bottom: 1px solid {p['border_soft']}; }}
    QTableWidget::item:selected {{ background: {p['accent_soft']}; color: {p['text']}; }}
    QCheckBox {{ spacing: 9px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; }}
    QSlider::groove:horizontal {{ height: 5px; background: {p['border']}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {p['accent']}; width: 16px; margin: -6px 0; border-radius: 8px; }}
    QProgressBar {{ border: 0; background: {p['border']}; border-radius: 1px; }}
    QProgressBar::chunk {{ background: {p['accent']}; border-radius: 1px; }}
    QScrollArea {{ border: 0; background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p['border']}; min-height: 36px; border-radius: 5px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QToolTip {{ background: {p['surface']}; border: 1px solid {p['border']}; padding: 6px; }}
    """


def app_icon(size: int = 64) -> QIcon:
    logo_path = Path(__file__).resolve().parent.parent / "assets" / "mind-logo-final.png"
    source = QPixmap(str(logo_path))
    if not source.isNull():
        pixmap = source.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return QIcon(pixmap)

    pixmap = QPixmap(size, size)
    pixmap.fill(QColor("transparent"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(2, 2, size - 4, size - 4, size * 0.22, size * 0.22)
    painter.fillPath(path, QColor("#17B6AA"))
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setFamily("Segoe UI Variable")
    font.setBold(True)
    font.setPixelSize(int(size * 0.47))
    painter.setFont(font)
    painter.drawText(pixmap.rect(), 0x84, "M")
    painter.end()
    return QIcon(pixmap)
