# Thickness Monitoring Project

This repository contains the React frontend and the Flask/Socket.IO backend for the thickness monitoring system.

## Frontend

The frontend lives in the repository root `src/` folder and is built with Vite.

```bash
npm install
npm run dev
```

For production builds:

```bash
npm run build
```

## Backend

The backend lives in `backend/` and serves the API, Socket.IO stream, and the built frontend from `dist/`.

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python cd22_server.py
```

The backend expects PostgreSQL plus the sensor hardware/network configuration defined in `backend/cd22_server.py`.

## Layout

- `src/` - frontend source
- `backend/` - Flask backend source
- `dist/` - generated frontend build output
