# Pistis

AI-powered quiz generation and practice platform built with FastAPI + PostgreSQL + Vanilla JS.

---

## Features

- **Manual Question Input** – Create MCQ, True/False, or Short Answer questions grouped into courses
- **AI Question Generation** – Upload PDF or DOCX files, or paste text; Groq LLaMA generates questions automatically
- **My Questions** – Browse, practice, and share quizzes; exam mode and practice mode
- **Performance Analysis** – Score trends, per-course analytics, attempt history with Chart.js graphs

---

## Project Structure

```
Pistis/
├── backend/          FastAPI application
│   ├── app/
│   │   ├── models/   SQLAlchemy ORM models
│   │   ├── schemas/  Pydantic schemas
│   │   ├── routers/  API route handlers
│   │   ├── services/ Business logic (AI, analytics)
│   │   └── utils/    Security, file parsing
│   ├── requirements.txt
│   └── .env.example
└── frontend/         Plain HTML + CSS + Vanilla JS
    ├── index.html         Login / Register
    ├── dashboard.html
    ├── input-questions.html
    ├── ai-generate.html
    ├── my-questions.html
    ├── quiz-practice.html
    ├── performance.html
    ├── css/
    └── js/
```

---

## Setup

### 1. Prerequisites

- Python 3.10+
- PostgreSQL (running locally)
- A Groq API key — get one free at https://console.groq.com

### 2. Create the PostgreSQL database

```bash
psql -U postgres -c "CREATE DATABASE ossyquiz;"
```

### 3. Configure environment variables

```bash
cd backend
cp .env.example .env
```

Edit `backend/.env`:

```env
DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/ossyquiz
SECRET_KEY=your_random_64_char_secret
GROQ_API_KEY=your_groq_api_key
ACCESS_TOKEN_EXPIRE_DAYS=30
```

Generate a secure `SECRET_KEY`:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 4. Install Python dependencies

```bash
cd backend
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 5. Start the backend

```bash
cd backend
uvicorn app.main:app --reload
```

The API starts at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

### 6. Open the frontend

Open `frontend/index.html` in your browser directly, or serve it with any static file server:

```bash
# Using Python's built-in server
cd frontend
python -m http.server 3000
# Then visit http://localhost:3000
```

> **Note:** Because the JS files use ES modules (`import`/`export`), you must serve them through a local server rather than opening `file://` directly in most browsers.

---

## API Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Create account |
| POST | `/api/auth/login` | Login → JWT |
| GET  | `/api/auth/me` | Current user |
| GET/POST | `/api/courses` | List / create courses |
| GET/POST | `/api/quizzes` | List / create quizzes |
| GET/POST | `/api/quizzes/{id}/questions` | Quiz questions |
| POST | `/api/generate/from-text` | AI generate from text |
| POST | `/api/generate/from-pdf` | AI generate from PDF |
| POST | `/api/generate/from-docx` | AI generate from DOCX |
| POST | `/api/attempts` | Start attempt |
| PUT  | `/api/attempts/{id}/submit` | Submit answers |
| GET  | `/api/analytics/overview` | Dashboard stats |
| GET  | `/api/analytics/trend` | Score trend |

Full interactive docs at `http://localhost:8000/docs` (Swagger UI).

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend API | FastAPI 0.111 |
| Database | PostgreSQL + SQLAlchemy 2.0 |
| Auth | JWT (python-jose) + bcrypt (passlib) |
| AI | Groq API – llama-3.3-70b-versatile |
| File parsing | pdfplumber (PDF), python-docx (DOCX) |
| Frontend | HTML5 + CSS3 + Vanilla JS (ES Modules) |
| Charts | Chart.js 4.4 (CDN) |
