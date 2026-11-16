# Changelog

All notable changes to ClipHUB. Format follows Keep a Changelog.

## [Unreleased]

### Changed
- New "liquid glass" look inspired by Apple apps: floating frosted sidebar and queue rail, rim-lit glass cards over a soft colour backdrop, capsule buttons, Apple-style segmented controls and toggles, system blue accent.
- Minimal pages: title-only headers, explanatory sub-text and hints removed (the model download note is now a tooltip), content in one centred column.

- Fonts: SF Pro + Sukhumvit Set on macOS; on other platforms the bundled open look-alikes Inter + Noto Sans Thai (OFL, `assets/fonts/`).

- All page styles now come from one theme in `ui/theme.py`; per-page stylesheets were removed and shared widgets live in `ui/widgets.py`.

### Added
- Light / dark / follow-system appearance switch at the bottom of the sidebar. The choice is saved and the native title bar follows it.
- Floating progress pill at the bottom of the window for the running job: name, status, progress bar, percent, how many jobs are waiting, and cancel.
- Themed toast notifications replace the old message boxes for finished jobs, errors and subtitle export, with "Show in folder" / "View transcript" actions. Errors stay until closed.
- The queue lists the running and newest jobs at the top.
- Animations: sliding segmented-control thumb, page fade, light/dark cross-fade.
- Liquid-glass buttons (gradient with a lit top edge) and rounded dropdown popups.
- "Clear finished" button in the task queue.
- Ctrl+1 to Ctrl+4 shortcuts to switch between tools.
