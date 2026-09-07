# FactPulse — frontend

Next.js (App Router) + TypeScript + Tailwind CSS. Runs on port 3000 and talks
to the FastAPI backend on port 8000.

```bash
npm install
cp .env.local.example .env.local
npm run dev
```

Set `NEXT_PUBLIC_API_BASE_URL` in `.env.local` if the backend is not on
`http://127.0.0.1:8000`.

Currently a scaffold: a "Hello FactPulse" landing page plus a live indicator
for backend reachability. See the [root README](../README.md) and
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md).
