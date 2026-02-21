# 03 — App Shell & Side Nav

**Phase**: 2 — App Shell & Layout
**Dependencies**: 02
**Status**: done

## Summary

Build the application shell with a collapsible sidebar, top bar, theme toggle (light/dark), and responsive layout. Port the patterns from the News repo.

## Requirements

### Reference
Port the side nav pattern from `/home/george/PycharmProjects/News/frontend/NewsFE/src/`. Key files to study:
- `css/App.css` — CSS variables and theme definitions
- Layout components for sidebar, topbar, and page content

### CSS Variables Theming
Define CSS custom properties in `:root` using RGB tuple format for flexibility:

```css
:root {
    /* Layout */
    --sidebar-width: 220px;
    --sidebar-collapsed-width: 60px;
    --topbar-height: 48px;

    /* Colors (light theme) */
    --primary-color: 245, 245, 245;
    --secondary-color: 255, 255, 255;
    --tertiary-color: 230, 230, 235;
    --soft-text: 75, 75, 75;
    --hard-text: 25, 25, 25;
    --accent: 59, 130, 246;
    --soft-border: 200, 200, 205;
    --soft-border-alpha: 0.3;
    --color-success: 34, 197, 94;
    --color-error: 239, 68, 68;
    --color-warning: 234, 179, 8;
}

[data-theme="dark"] {
    --primary-color: 24, 24, 27;
    --secondary-color: 39, 39, 42;
    --tertiary-color: 52, 52, 56;
    --soft-text: 161, 161, 170;
    --hard-text: 228, 228, 231;
    /* ... etc */
}
```

### Sidebar Component
- Collapsible: toggle between expanded (shows icon + label) and collapsed (icon only)
- Navigation links:
  - Dashboard (home)
  - Tickers
  - Subreddits
  - Scraper Monitor
- Sidebar state persisted in localStorage
- Active route highlighted

### Top Bar Component
- App title/logo on the left
- Theme toggle button (sun/moon icon) on the right
- Theme preference persisted in localStorage

### Responsive Layout
- Desktop: Sidebar fixed on the left, content fills remaining width
- Mobile (< 768px): Sidebar becomes a drawer overlay with backdrop
- Hamburger menu button in top bar on mobile
- Smooth transitions on sidebar open/close

### Page Content Area
- Wrap route content in a container that adjusts for sidebar width
- Scrollable independently from sidebar

## Acceptance Criteria

- [ ] Sidebar renders with navigation links and collapses/expands
- [ ] Theme toggle switches between light and dark mode
- [ ] Both sidebar state and theme preference persist across page reloads
- [ ] Layout is responsive — drawer on mobile, fixed sidebar on desktop
- [ ] Active navigation link is visually highlighted
- [ ] All colors use CSS variables — no hardcoded color values in components
- [ ] Smooth transitions on sidebar toggle and theme switch

## Technical Notes

- Use `react-icons/fi` (Feather icons) for nav icons — clean and consistent.
- The News repo uses `rgb(var(--color-name))` pattern which allows opacity modifiers: `rgba(var(--color-name), 0.5)`. Preserve this pattern.
- Store theme in a `data-theme` attribute on `<html>` for CSS selector targeting.
