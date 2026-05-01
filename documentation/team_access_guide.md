# ARIA — Team Access Guide
**Author:** Sahil Khalsa
**Last updated:** 2026-04-30
**Scope:** Running and accessing the clinician dashboard, patient PWA, and backend API — on desktop and phone.

---

## How it works

Three services run together:

| Service | Port | Who uses it |
|---|---|---|
| Backend API | `8000` | All apps call this |
| Clinician dashboard | `3000` | Clinician role (dr.frank) |
| Patient PWA | `3001` | Patient demo (research ID 1091) |

One person (the **host**) runs everything on their laptop. Everyone else connects over WiFi or via a public tunnel.

---

## Option A — Same WiFi (recommended for demo day)

### Host setup

**Step 1 — Pull latest code**
```powershell
git pull origin sahil/dev
```

**Step 2 — Run DB migration** (only needed once)
```powershell
cd D:\ARIA_Workspace\ARIA
D:\conda\python.exe scripts/setup_db.py
```
Look for `[OK] Index/migration: readings_symptoms_col` to confirm it ran.

**Step 3 — Find your laptop IP**
```powershell
ipconfig
```
Note the **IPv4 Address** under your WiFi adapter — e.g. `192.168.1.45`

**Step 4 — Create `patient-app/.env.local`**
```
NEXT_PUBLIC_API_URL=http://192.168.1.45:8000
```
Replace `192.168.1.45` with your actual IP.

**Step 5 — Start all three services** (3 separate terminals)
```powershell
# Terminal 1 — Backend
cd D:\ARIA_Workspace\ARIA\backend
D:\conda\python.exe -m uvicorn app.main:app --reload --port 8000

# Terminal 2 — Clinician dashboard
cd D:\ARIA_Workspace\ARIA\frontend
npm run dev

# Terminal 3 — Patient PWA
cd D:\ARIA_Workspace\ARIA\patient-app
npm install
npm run dev
```

**Step 6 — Share these URLs with the team**

| What | URL |
|---|---|
| Clinician dashboard | `http://192.168.1.45:3000` |
| Patient PWA | `http://192.168.1.45:3001` |
| API docs (Swagger) | `http://192.168.1.45:8000/docs` |

---

### Team member setup (everyone else)

**Desktop — no installation needed:**
- Clinician dashboard → open `http://192.168.1.45:3000` in any browser
- Patient PWA → open `http://192.168.1.45:3001` in any browser

**Phone:**
1. Connect your phone to the **same WiFi** as the host laptop
2. Open Safari (iOS) or Chrome (Android)
3. Navigate to `http://192.168.1.45:3001`
4. Log in with Research ID `1091`

**Install PWA to home screen (optional but recommended):**
- **Android Chrome** → tap the 3-dot menu → "Add to Home Screen"
- **iOS Safari** → tap the Share button → "Add to Home Screen"

The app will launch full-screen with no browser chrome, like a native app.

---

## Option B — Everyone runs locally (for development)

Each team member runs the full stack on their own machine against the shared Supabase database.

**Step 1 — Pull latest code**
```powershell
git pull origin sahil/dev
```

**Step 2 — Get the `.env` file**

Get `backend/.env` from Sahil. It contains `DATABASE_URL`, `PATIENT_JWT_SECRET`, and API keys. Do not commit this file — it is in `.gitignore`.

**Step 3 — Run migration** (once per machine)
```powershell
cd D:\ARIA_Workspace\ARIA
D:\conda\python.exe scripts/setup_db.py
```

**Step 4 — Create `patient-app/.env.local`**
```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

**Step 5 — Start everything**
```powershell
# Terminal 1 — Backend
cd D:\ARIA_Workspace\ARIA\backend
D:\conda\python.exe -m uvicorn app.main:app --reload --port 8000

# Terminal 2 — Clinician dashboard
cd D:\ARIA_Workspace\ARIA\frontend
npm install
npm run dev

# Terminal 3 — Patient PWA
cd D:\ARIA_Workspace\ARIA\patient-app
npm install
npm run dev
```

**Access locally:**

| What | URL |
|---|---|
| Clinician dashboard | `http://localhost:3000` |
| Patient PWA | `http://localhost:3001` |
| API docs (Swagger) | `http://localhost:8000/docs` |

---

## Option C — Remote access without shared WiFi (ngrok)

Use this for remote teammates or screen-sharing demos where participants are not on the same network.

**Step 1 — Install ngrok**
```powershell
winget install ngrok
```

**Step 2 — Expose backend and patient app** (2 terminals)
```powershell
# Terminal A
ngrok http 8000

# Terminal B
ngrok http 3001
```

Each gives a public HTTPS URL like `https://abc123.ngrok.io`

**Step 3 — Update `patient-app/.env.local`** with the backend ngrok URL
```
NEXT_PUBLIC_API_URL=https://abc123.ngrok.io
```
Restart `npm run dev` after changing this.

**Step 4 — Share the patient app ngrok URL** — anyone on any network can open it in a browser, including on their phone.

---

## Login credentials

| App | Login | Details |
|---|---|---|
| Clinician dashboard | Username: `dr.frank` | Use the password set during setup |
| Patient PWA | Research ID: `1091` | Demo patient — therapeutic inertia scenario |

---

## What each role sees

| Role | App | What they can do |
|---|---|---|
| Clinician | Dashboard (`3000`) | View patient list, briefings, alerts, acknowledge alerts |
| Patient (demo) | PWA (`3001`) | Submit BP readings, confirm medications, download .ics reminders |

Patient data submitted via the PWA (BP readings, medication confirmations) feeds directly into the clinician briefing and pattern engine — visible on the clinician dashboard after the next pattern recompute.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Phone can't connect to the app | Confirm phone and laptop are on the same WiFi network |
| CORS error in browser | Ensure `NEXT_PUBLIC_API_URL` in `patient-app/.env.local` uses the host laptop IP, not `localhost` |
| `ModuleNotFoundError: No module named 'jwt'` on backend start | Run with `D:\conda\python.exe`, not the system Python (`C:\Python313`) |
| Patient app shows blank page | Check backend is running on port 8000 before starting the patient app |
| `.ics` download fails | Confirm `PATIENT_JWT_SECRET` is set in `backend/.env` |
| `conda activate` does not change Python | Run `D:\conda\Scripts\conda.exe init powershell`, then reopen the terminal |
| Port already in use | Another process is using 8000/3000/3001 — close it or change the port |
