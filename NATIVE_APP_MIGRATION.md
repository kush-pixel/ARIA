# ARIA Patient App — Native Migration Analysis
## PWA vs Native: What Changes, What It Costs
## May 2026

---

## What the Current App Is

Next.js 14 PWA installed via `@ducanh2912/next-pwa`. It runs in the browser, works on the home screen when installed, and uses web APIs for everything — Web Push, WebAuthn, IndexedDB, Service Worker, Web Bluetooth. It is essentially a website pretending to be an app.

---

## The Right Native Choice for This Team

**React Native with Expo** — not Flutter, not Swift/Kotlin.

The team already writes React and TypeScript. Expo handles 90% of native complexity through its SDK (camera, biometrics, calendar, push, BLE). The code structure is similar enough that the transition is not a full relearn — it is a paradigm shift, not a language change.

---

## What Changes

### Things That Stay Exactly the Same

- The entire backend — FastAPI, all API endpoints, database schema, JWT auth
- The clinician dashboard — completely unaffected
- Business logic — same API calls, same data structures

---

### Things That Get Replaced

| Current (PWA) | Native Equivalent | Easier or Harder? |
|---|---|---|
| Web Push API | APNs (Apple) + FCM (Firebase/Google) via Expo Notifications | **Easier** — more reliable, no browser dependency, works when app is closed |
| WebAuthn (Face ID / Touch ID) | `expo-local-authentication` | **Easier** — one function call vs full challenge-response protocol |
| IndexedDB offline queue | AsyncStorage or SQLite | **Similar** — different API, same concept |
| Service Worker (offline) | Expo built-in offline handling | **Easier** — no service worker complexity |
| Web Bluetooth API | `react-native-ble-plx` | **Easier** — direct BLE, no browser permission quirks |
| Google ML Kit (OCR) via browser | `expo-camera` + ML Kit native SDK | **Easier** — full camera control, no browser sandbox |
| Google Calendar (OAuth web) | `react-native-calendar-events` | **Easier** — direct OS calendar access, no OAuth dance |
| Apple Calendar (CalDAV web) | Same `react-native-calendar-events` | **Easier** — direct EventKit access |
| `manifest.json` + PWA config | `app.json` (Expo config) | Similar |
| Next.js router | React Navigation | **Different paradigm** — stack navigator, tab navigator, drawer |
| `localStorage` / `sessionStorage` | `AsyncStorage` (encrypted) | Similar |
| CSS / Tailwind | StyleSheet / NativeWind | **Different** — no CSS box model |
| HTML elements (`div`, `input`) | Native components (`View`, `TextInput`) | **Different** |

---

### Things That Become Possible (Not Possible in PWA)

- **Apple HealthKit** — read/write BP readings directly to Apple Health. A patient's BP automatically appears in their Health app. No extra work for the patient.
- **Google Health Connect** — same for Android
- **Direct Wearable SDK access** — Withings, Omron, and Apple Watch have native SDKs that are significantly more reliable than their web APIs
- **Background tasks** — sync readings while the app is closed, not just when the browser is open
- **Haptic feedback** — tactile confirmation on BP submission, important for elderly users
- **App Store discoverability** — patients can find ARIA in the App Store, not just via a URL
- **Home screen widget** — shows today's tasks and streak without opening the app

---

### Things That Get Harder

- **Over-the-air updates** — web deploys instantly; native requires App Store review (1–3 days for Apple, 1–2 days for Google). Critical bug fixes take longer to reach users.
- **App Store review** — Apple is strict about healthcare apps. Every submission requires a review. First submission for a healthcare app can take 5–7 days. Apple may ask for IRB approval documentation or clinician verification.
- **Two platforms** — iOS and Android must both be tested on physical devices. Behaviour differs especially for Push Notifications and BLE.
- **React Native debugging** — different toolchain, Metro bundler, Xcode and Android Studio required for native builds.

---

## What Has to Be Rewritten

### Complete Rewrite

The entire `patient-app/` directory. Every page, every component, every style. The existing Next.js code is discarded.

| Current File | What Replaces It |
|---|---|
| `src/app/page.tsx` (login) | `screens/LoginScreen.tsx` |
| `src/app/submit/page.tsx` | `screens/ReadingScreen.tsx` |
| `src/app/confirm/page.tsx` | `screens/MedicationsScreen.tsx` |
| `src/lib/api.ts` | Same API calls, moved to RN context |
| `src/lib/auth.ts` | Same JWT logic, AsyncStorage instead of localStorage |
| `public/manifest.json` | `app.json` |
| Service worker | Removed entirely |
| `@ducanh2912/next-pwa` | Removed entirely |

### Stays the Same

- `backend/` — zero changes
- `frontend/` (clinician dashboard) — zero changes
- All API contracts — no changes

---

## App Store Requirements

### Apple App Store

- **Apple Developer Program:** $99/year — must be enrolled before any TestFlight or App Store distribution
- **Review timeline:** 1–3 days for standard apps; 5–7 days for first submission of a healthcare app
- **Healthcare scrutiny:** Apple reviews medical apps carefully. ARIA needs to clearly state it is clinical decision support for physicians, not a patient-facing diagnostic tool. The patient app (reading submission, medication confirmation) is lower risk than the clinician side — but any mention of "blood pressure monitoring" triggers health category review
- **Privacy nutrition label:** Must disclose every data type collected. BP readings, medication data, and health metrics all require disclosure

### Google Play Store

- **One-time registration:** $25
- **Review timeline:** 1–2 days
- **Less strict** than Apple for the healthcare category

---

## How Much More Work

### Setup Overhead (One-Time)

| Task | Time |
|---|---|
| Expo project initialisation, EAS Build configuration, app signing | 2 days |
| Apple Developer Program enrollment and provisioning | 1 day |
| Google Play Console setup | 4 hours |
| App Store / Play Store first submission and review process | 1 week |

### Rewriting Existing 3 PWA Pages in React Native

The current app has 3 pages (login, submit, confirm). Rewriting these in React Native before adding new Sprint 5 features: **1 week**

### Sprint 5 Features — Native vs PWA Effort Comparison

| Feature | PWA Effort | Native Effort | Difference |
|---|---|---|---|
| Home hub | 2 days | 2 days | Same |
| Guided BP measurement | 2 days | 2 days | Same |
| Biometric login | 3 days (WebAuthn complex) | 1.5 days (expo-local-auth simple) | **1.5 days faster** |
| Push notifications | 2 days | 1 day (APNs/FCM more straightforward) | **1 day faster** |
| Offline support | 2 days | 1 day (no service worker complexity) | **1 day faster** |
| Missed dose + late confirmation | 1 day | 1 day | Same |
| Submission feedback | 4 hours | 4 hours | Same |
| OCR camera scan | 2 days | 1 day (full camera control) | **1 day faster** |
| Daily tips | 1 day | 1 day | Same |
| Food suggestions | 4 hours | 4 hours | Same |
| Secure messaging | 2 days | 2 days | Same |
| Google + Apple Calendar | 2 days (OAuth web complex) | 1 day (direct OS access) | **1 day faster** |
| Streaks system | 2 days | 2 days | Same |
| Accessibility | 3 days | 2 days (native a11y APIs better) | **1 day faster** |
| **Total** | **26.5 days** | **20 days** | **6.5 days faster** |

---

### Net Additional Work

| Item | Time |
|---|---|
| Setup, signing, app store accounts | 3 days |
| Rewrite existing 3 PWA pages | 1 week |
| App store submission and review (first time) | 1 week |
| Physical device testing (iOS + Android both required) | 3 days |
| React Native learning curve / toolchain setup | 3 days |
| **Total added** | **~4 weeks** |
| Features that become easier | **−6.5 days** |
| **Net addition to timeline** | **~3 weeks** |

---

## Impact on Timeline

| | PWA Plan | Native Plan |
|---|---|---|
| Patient app sprint | Weeks 12–13 | Weeks 12–15 |
| Clinician Workflow sprint | Week 15 | Week 16–17 |
| Product sprint | Week 16 | Week 18–19 |
| **Total duration** | **16 weeks** | **19 weeks** |

---

## Recommendation

### Go Native If Any of These Are True

- Real elderly patients will use the app — native accessibility is meaningfully better
- The Withings/wearable integration is a priority — native BLE and device SDKs are far more reliable than web APIs
- Apple Health / Google Health Connect integration is wanted — only possible natively
- Long-term product vision is a consumer health app — App Store presence matters for adoption

### Stay PWA If Any of These Are True

- The summer study uses de-identified patients on controlled devices — PWA is sufficient for this
- The 16-week timeline is firm — going native costs 3 weeks
- The team has no native mobile experience and the learning curve is a risk

---

## Honest Assessment for This Summer

The PWA is sufficient for the physician validation study with de-identified patients. The native rewrite is the right long-term call but adds 3 weeks to a plan that is already 16 weeks.

**Recommended approach:** Finish the PWA this summer. Use the physician validation study to confirm which native-only features (HealthKit, direct BLE, background sync) are actually needed based on real physician and patient feedback. Plan the native rewrite as a dedicated post-summer initiative with a proper sprint allocation and the validation data to justify the investment.

---

*Document prepared: May 2026*
*ARIA | Leap of Faith Technologies | IIT CS 595 Spring 2026*
