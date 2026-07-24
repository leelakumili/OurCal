# Android — spike findings

**Date:** 2026-07-23 · **Verdict: BeeWare/Briefcase works.** One Python codebase
can target both macOS and Android.

A throwaway Briefcase app was built and run on a real Android phone to answer a
single question: *does Google OAuth complete on Android?* It does. The spike and
its APK were deleted afterwards; this file is what it bought us.

## What was proven on-device

| Unknown | Result |
|---|---|
| CPython 3.11 runs on Android | ✅ via Chaquopy, which Briefcase uses |
| `google-api-python-client` / `google-auth-oauthlib` import | ✅ 621 files bundled, no special handling |
| A loopback HTTP server binds inside an Android app | ✅ |
| OAuth consent screen opens | ✅ once the browser launch is fixed (below) |
| **The redirect reaches the app's loopback listener** | ✅ **Chrome rendered the app's own "authentication flow has completed" page at `localhost:37545`** |
| Token exchange | ❌ blocked by backgrounded-app DNS — see below |

## The two problems, and their fixes

### 1. `webbrowser` cannot open a browser on Android

`google_auth_oauthlib` calls `webbrowser.get().open(auth_url)`. Python's stdlib
module hunts for Unix browser binaries (`xdg-open`, `firefox`) that Android does
not have, and raises `webbrowser.Error: could not locate runnable browser`.

Fix — satisfy the same interface with an Android Intent:

```python
class _AndroidBrowser:
    def open(self, url, new=0, autoraise=True):
        from com.chaquo.python import Python
        from java import jclass
        Intent, Uri = jclass("android.content.Intent"), jclass("android.net.Uri")
        ctx = Python.getPlatform().getApplication()
        intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK)   # required from a non-activity context
        ctx.startActivity(intent)
        return True

webbrowser.get = lambda *a, **k: _AndroidBrowser()
```

### 2. DNS dies while the app is backgrounded — the real design constraint

Measured directly, before and after the browser trip:

```
[before]         DNS ok -> 2607:f8b0:400e:c09::5f
[before]         HTTPS returned HTTP 404          (a real response; the socket worked)
[after-redirect] DNS FAILED: gaierror: [Errno 7] No address associated with hostname
```

Android revokes the process's network binding while it is in the background.
`run_local_server()` exchanges the authorization code for a token *the instant
the redirect arrives* — which is precisely when the user is still looking at
Chrome and the app has no network.

Fix: **split the flow.** Catch the authorization code, let the user return to
the app, then exchange it in the foreground. Do not use `run_local_server()`
unmodified on Android.

## Other constraints worth designing around

- **UI updates must happen on the main thread.** Touching a view from a worker
  raises `ViewRootImpl$CalledFromWrongThreadException`. The OAuth wait must run
  off the UI thread (it blocks), so results have to be marshalled back.
- **Play Protect blocks sideloaded APKs** — "App blocked to protect your
  device", escaped via the understated *Install anyway* text. This is Android's
  counterpart to macOS Gatekeeper's "OurCal is damaged", and signing does not
  remove it; only Play Store distribution does ($25 one-time developer account).
- **Size and build cost:** the spike APK was 65–84MB (CPython + Google libs).
  First build pulls a JDK, the Android SDK and Gradle — several GB and several
  minutes. **CI must cache `~/.gradle` and the SDK** or every release will pay
  that again.
- **Briefcase requires a PEP 639 `license` field** in `pyproject.toml` or it
  refuses to build.

## The icon carries over

`packaging/icon.html` is deliberately HTML rather than a binary, so it renders at
any size. The same artwork produces Android's launcher icons — `mipmap` at
48/72/96/144/192px plus the adaptive foreground layer — with the same
`sips`-based pipeline already in `packaging/build-app.sh`. No second icon source.

## Shape of the migration, when it happens

1. Move `ourcal.py` into a Briefcase project (`pyproject.toml` describing both targets).
2. Replace the browser launch per fix 1; split the OAuth flow per fix 2.
3. Point a WebView at the in-process server — the existing `PAGE` is already
   responsive (viewport tag, 560/640px breakpoints), so the UI needs little work.
4. Add the Android target and an `.apk` release job with aggressive caching.
5. Briefcase would likely own macOS packaging too, replacing
   `packaging/build-app.sh` and the current release workflow.

The app code, the Edit feature, `merge_events`, the data-directory handling and
all 206 tests survive the migration untouched. That single-codebase property is
the whole reason for choosing Briefcase over reimplementing the backend in
JavaScript — a second implementation would double the surface for exactly the
class of bug that cost us an hour tonight (one field handled in `normalize` but
not in `merge_events`).
