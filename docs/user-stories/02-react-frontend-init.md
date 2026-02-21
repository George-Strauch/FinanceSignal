# 02 — React Frontend Init

**Phase**: 1 — Project Scaffolding
**Dependencies**: 01 (FastAPI must be running for proxy)
**Status**: done

## Summary

Scaffold a React frontend using Vite under `frontend/`. Install core dependencies and configure a dev proxy to the FastAPI backend.

## Requirements

### Project Setup
- Use Vite with the React template (JavaScript, not TypeScript unless user prefers)
- Create under `frontend/` at project root
- Clean out Vite boilerplate (default App content, logos)

### Dependencies to Install
- `react-router-dom` — Client-side routing
- `react-icons` — Icon library
- `recharts` — Charting library for data visualizations

### Dev Proxy
- Configure Vite's `server.proxy` to forward `/api/*` requests to `http://localhost:8000`
- This allows the frontend to call backend endpoints without CORS issues in development

### Project Structure
```
frontend/
├── index.html
├── package.json
├── vite.config.js
├── public/
└── src/
    ├── main.jsx         # Entry point with BrowserRouter
    ├── App.jsx          # Root component with route definitions
    ├── App.css          # Global styles (will be fleshed out in story 03)
    ├── components/      # Shared components directory
    ├── pages/           # Page components directory
    └── api/             # API client utilities
        └── client.js    # Fetch wrapper for /api calls
```

### API Client (`src/api/client.js`)
- Simple fetch wrapper that:
  - Prepends `/api` to paths
  - Parses JSON responses
  - Throws on non-OK status codes
  - Exports `get`, `post`, `del` helper functions

### Minimal App
- `App.jsx` should render a placeholder page that fetches and displays the backend's `GET /` response to verify connectivity

## Acceptance Criteria

- [ ] `cd frontend && npm run dev` starts Vite dev server on port 5173
- [ ] Proxy correctly forwards `/api` requests to FastAPI on port 8000
- [ ] Placeholder page displays backend API response
- [ ] `react-router-dom`, `react-icons`, `recharts` are in `package.json`
- [ ] `api/client.js` provides working fetch helpers
- [ ] No Vite boilerplate content remains

## Technical Notes

- The News repo (`/home/george/PycharmProjects/News`) uses a similar Vite + React setup — reference it for patterns but don't copy wholesale.
- Keep CSS minimal here; the full theming system comes in story 03.
