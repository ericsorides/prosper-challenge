"""Healthie EHR integration (Playwright).

Targets **secure.gethealthie.com** (English provider UI): Frontegg email/password then **Log In**,
clients list ``form[role=search]``, profile links under ``/users/{id}``, **Book session**, and the
book-session modal (``data-testid="modal-content"``, react-datepicker, ``primaryButton``).
"""

import calendar
import os
import re
import time
from datetime import datetime

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright

_browser: Browser | None = None
_context: BrowserContext | None = None
_page: Page | None = None

async def login_to_healthie() -> Page:
    """Log into Healthie and return an authenticated page instance.

    This function handles the login process using credentials from environment
    variables. The browser and page instances are stored for reuse by other
    functions in this module.

    Returns:
        Page: An authenticated Playwright Page instance ready for use.

    Raises:
        ValueError: If required environment variables are missing.
        Exception: If login fails for any reason.
    """
    global _browser, _context, _page

    email = os.environ.get("HEALTHIE_EMAIL")
    password = os.environ.get("HEALTHIE_PASSWORD")

    if not email or not password:
        raise ValueError("HEALTHIE_EMAIL and HEALTHIE_PASSWORD must be set in environment variables")

    if _page is not None:
        logger.info("Using existing Healthie session")
        try:
            await _page.goto("https://secure.gethealthie.com/clients", wait_until="domcontentloaded")
            try:
                await _page.wait_for_load_state("load", timeout=15000)
            except Exception:
                pass
            await _ensure_page_hydrated(_page, timeout_ms=25000, reason="session_clients_not_hydrated")
            await _wait_until_clients_page_ready(_page, timeout_ms=20000)
            if "login" not in _page.url and "sign_in" not in _page.url:
                return _page
            logger.warning("Existing session is not authenticated anymore, re-authenticating")
        except Exception:
            logger.warning("Existing Healthie page is unusable, re-authenticating")

    logger.info("Logging into Healthie...")
    playwright = await async_playwright().start()
    headless = os.environ.get("HEALTHIE_HEADLESS", "false").lower() == "true"
    _browser = await playwright.chromium.launch(headless=headless)
    _context = await _browser.new_context()
    _page = await _context.new_page()

    await _page.goto("https://secure.gethealthie.com/users/sign_in", wait_until="domcontentloaded")
    # Healthie's auth UI is a SPA and can take several seconds to hydrate.
    # We wait for any likely login field to appear instead of using networkidle.
    await _wait_for_login_form(_page, timeout_ms=30000)
    logger.info("Healthie login page loaded: {}", _page.url)

    # Frontegg often uses name="email" with type="text", not type="email".
    email_input = await _healthie_visible_login_field(
        _page,
        [
            'input[type="email"]',
            'input[name="email"]',
            'input[name="Email"]',
            'input[autocomplete="email"]',
            'input[autocomplete="username"]',
        ],
        "email",
        timeout_ms=20000,
    )
    await email_input.click()
    await email_input.fill("")
    await email_input.fill(email)
    await email_input.evaluate(
        """el => {
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }"""
    )

    password_input = _page.locator('input[type="password"]').first
    try:
        await password_input.wait_for(state="visible", timeout=2000)
    except Exception:
        await _submit_email_step(_page)
        await password_input.wait_for(state="visible", timeout=12000)
    await password_input.fill(password)

    try:
        login_btn = _page.get_by_role("button", name="Log In").first
        await login_btn.wait_for(state="visible", timeout=8000)
        await login_btn.click()
    except Exception:
        sub = _page.locator('form button[type="submit"]').first
        await sub.wait_for(state="visible", timeout=5000)
        await sub.click()

    await _page.wait_for_timeout(3500)
    await _page.wait_for_load_state("domcontentloaded")
    current_url = _page.url
    if "login" in current_url or "sign_in" in current_url:
        raise Exception(f"Login may have failed; still on auth page ({current_url})")

    logger.info("Successfully logged into Healthie")
    return _page



async def find_patient(name: str, date_of_birth: str) -> dict | None:
    """Find a patient in Healthie by name and date of birth.

    Args:
        name: The patient's full name.
        date_of_birth: The patient's date of birth in a format that Healthie accepts.

    Returns:
        dict | None: A dictionary containing patient information if found,
            including at least a 'patient_id' field. Returns None if the patient
            is not found or if an error occurs.

    Example return value:
        {
            "patient_id": "12345",
            "name": "John Doe",
            "date_of_birth": "1990-01-15",
            ...
        }
    """
    page = await login_to_healthie()
    normalized_name = " ".join(name.strip().split())
    normalized_dob = _normalize_date(date_of_birth)
    dob_variants = _date_variants(normalized_dob or date_of_birth)

    try:
        await page.goto("https://secure.gethealthie.com/clients", wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("load", timeout=15000)
        except Exception:
            pass
        await _ensure_page_hydrated(page, timeout_ms=30000, reason="clients_page_not_hydrated")
        await _wait_until_clients_page_ready(page, timeout_ms=25000)
        search_input = await _wait_for_clients_search_input(page)
        if search_input:
            logger.info("Found patient search input on {}", page.url)

        if not search_input:
            logger.error("Could not locate patient search input on {}", page.url)
            return None

        await _submit_clients_list_search(page, search_input, normalized_name)
        await _wait_for_client_results(page, timeout_ms=25000, patient_name=normalized_name)

        candidates = await _collect_client_candidates(page, normalized_name)
        if len(candidates) < 1:
            logger.info("No client profile links after primary search; retrying with keystroke entry")
            await _fallback_search_by_keystrokes(page, search_input, normalized_name)
            await _wait_for_client_results(page, timeout_ms=20000, patient_name=normalized_name)
            candidates = await _collect_client_candidates(page, normalized_name)

        if len(candidates) < 1:
            logger.info("No patient rows found for name '{}'", normalized_name)
            return None

        candidates = _filter_candidates_by_name(candidates, normalized_name)
        candidates = _sort_candidates_by_match(candidates, normalized_name, dob_variants)

        sole_list_match = (
            len(candidates) == 1
            and (candidates[0].get("text") or "").strip().lower() == normalized_name.lower()
            and bool(normalized_dob)
        )

        for cand in candidates[:10]:
            patient_id = cand["id"]
            href = cand.get("href") or ""
            text = (cand.get("text") or "").strip()
            profile_urls = list(
                dict.fromkeys(
                    [
                        _absolute_healthie_href(href),
                        f"https://secure.gethealthie.com/clients/{patient_id}",
                    ]
                )
            )

            body = ""
            for purl in profile_urls:
                await page.goto(purl, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("load", timeout=12000)
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
                page_text = await page.inner_text("body")
                body = _profile_body_normalized(page_text)
                if _dob_matches_profile_body(body, dob_variants, normalized_dob):
                    logger.info("Found matching patient '{}' ({})", text or normalized_name, patient_id)
                    return {
                        "patient_id": patient_id,
                        "name": text or normalized_name,
                        "date_of_birth": normalized_dob or date_of_birth,
                    }

            if text.lower() == normalized_name.lower() and not normalized_dob:
                logger.info("Found patient by name-only '{}' ({})", text, patient_id)
                return {
                    "patient_id": patient_id,
                    "name": text,
                    "date_of_birth": date_of_birth,
                }

            if sole_list_match and text.lower().strip() == normalized_name.lower():
                logger.warning(
                    "DOB not visible on profile pages for sole search match '{}'; accepting id {}",
                    normalized_name,
                    patient_id,
                )
                return {
                    "patient_id": patient_id,
                    "name": text or normalized_name,
                    "date_of_birth": normalized_dob or date_of_birth,
                }

        logger.info("Patient '{}' not found with DOB '{}'", normalized_name, normalized_dob)
        return None
    except Exception as exc:
        logger.exception("Failed to find patient: {}", exc)
        return None


async def _open_book_session_modal(page: Page, patient_id: str) -> bool:
    """Click Book session for this client (do not use /appointments/new — Healthie treats 'new' as search text)."""
    scoped = page.locator(f'[data-testid="book-session-with-{patient_id}"]')
    deadline = time.monotonic() + 40.0
    while time.monotonic() < deadline:
        try:
            first = scoped.first
            if await first.is_visible(timeout=1200):
                await first.scroll_into_view_if_needed()
                await first.click()
                return True
        except Exception:
            pass
        try:
            alt = page.get_by_role("button", name=re.compile(r"^book session$", re.I)).first
            if await alt.is_visible(timeout=1200):
                await alt.scroll_into_view_if_needed()
                await alt.click()
                return True
        except Exception:
            pass
        await page.wait_for_timeout(450)
    return False


HEALTHIE_OPTION_INITIAL = "Initial Consultation - 60 Minutes"
HEALTHIE_OPTION_FOLLOW_UP = "Follow-up Session - 45 Minutes"


def _healthie_booking_type_option_label(appointment_type: str | None) -> str:
    """Map tool enum / phrases to Healthie's exact option labels."""
    if not appointment_type or not str(appointment_type).strip():
        return HEALTHIE_OPTION_INITIAL
    t = str(appointment_type).strip().lower().replace("-", "_")
    if t in ("follow_up", "followup") or "follow" in t:
        return HEALTHIE_OPTION_FOLLOW_UP
    if t in ("initial_consultation", "initial", "consultation") or "initial" in t:
        return HEALTHIE_OPTION_INITIAL
    return HEALTHIE_OPTION_INITIAL


async def _select_healthie_appointment_type(page: Page, modal, appointment_type: str | None) -> bool:
    """Open **Appointment type** and pick Initial (60m) or Follow-up (45m). Required before submit."""
    desired = _healthie_booking_type_option_label(appointment_type)
    combo = modal.get_by_role("combobox", name=re.compile(r"appointment\s*type", re.I)).first
    try:
        if not await combo.is_visible(timeout=3000):
            combo = None
    except Exception:
        combo = None

    if combo is None:
        return False

    try:
        await combo.scroll_into_view_if_needed()
        await combo.click(timeout=5000)
        await page.wait_for_timeout(450)
        listbox = page.get_by_role("listbox").last
        opt = listbox.get_by_role("option", name=re.compile(re.escape(desired), re.I))
        await opt.click(timeout=6000)
        await page.wait_for_timeout(500)
        return True
    except Exception:
        pass

    try:
        await combo.click(timeout=3000)
        await page.wait_for_timeout(400)
        opt = page.get_by_role("option", name=re.compile(re.escape(desired), re.I)).first
        await opt.click(timeout=6000)
        await page.wait_for_timeout(500)
        return True
    except Exception:
        return False


async def _dismiss_healthie_datepicker_overlays(page: Page) -> None:
    """Close react-datepicker poppers (date + time) so the next field is actionable."""
    for _ in range(6):
        pop = page.locator(".react-datepicker-popper").first
        try:
            visible = await pop.is_visible(timeout=120)
        except Exception:
            visible = False
        if not visible:
            return
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(220)


async def _fill_healthie_booking_datetime(modal, page: Page, display_date: str, time: str) -> tuple[bool, bool]:
    """Fill Healthie book-session modal date/time (react-datepicker; time input has broken aria-labelledby)."""
    date_loc = modal.locator("input#date, input[name='date']").first
    time_loc = modal.locator("input#time, input[name='time']").first

    filled_date = False
    try:
        await date_loc.wait_for(state="visible", timeout=10000)
        await date_loc.click(timeout=5000)
        await date_loc.fill("")
        await date_loc.fill(display_date)
        await date_loc.evaluate(
            """el => {
              el.dispatchEvent(new Event("input", { bubbles: true }));
              el.dispatchEvent(new Event("change", { bubbles: true }));
            }"""
        )
        filled_date = True
    except Exception:
        pass

    await _dismiss_healthie_datepicker_overlays(page)
    await page.wait_for_timeout(350)

    filled_time = False
    try:
        await time_loc.wait_for(state="visible", timeout=10000)
        await time_loc.click(timeout=5000, force=True)
        await time_loc.fill("", force=True)
        await time_loc.fill(time, force=True)
        await time_loc.evaluate(
            """el => {
              el.dispatchEvent(new Event("input", { bubbles: true }));
              el.dispatchEvent(new Event("change", { bubbles: true }));
            }"""
        )
        filled_time = True
    except Exception:
        pass

    return filled_date, filled_time


async def _scroll_healthie_booking_modal_to_bottom(modal) -> None:
    """Scroll modal shell so **Add appointment** (often below the fold) is reachable."""
    try:
        await modal.evaluate("""(root) => {
          const scrollables = [root, ...root.querySelectorAll('*')].filter(
            (el) => {
              const s = window.getComputedStyle(el);
              return (s.overflowY === 'auto' || s.overflowY === 'scroll') && el.scrollHeight > el.clientHeight;
            }
          );
          for (const el of scrollables) {
            try { el.scrollTop = el.scrollHeight; } catch (e) {}
          }
          const form = root.querySelector('form');
          if (form) try { form.scrollTop = form.scrollHeight; } catch (e) {}
        }""")
    except Exception:
        pass


_ADD_APPOINTMENT_DOUBLE_GAP_MS = 480


async def _click_add_appointment_via_dom(page: Page) -> bool:
    """Activate **Add appointment** purely in the page DOM (no Playwright hit-testing).

    Healthie's React handler sometimes misses a single ``.click()``; this path scrolls the
    node, fires pointer/mouse events at the element center, calls ``click()``, and uses
    ``form.requestSubmit(button)`` when available.

    Fires **twice** with a short gap: the first interaction can commit time-field formatting;
    the second performs submit.
    """
    try:
        _gap = str(_ADD_APPOINTMENT_DOUBLE_GAP_MS)
        _dom_script = """
            async () => {
              const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toLowerCase();

              function roots() {
                const ids = ["modal-content", "modal", "modal-section"];
                const out = [];
                for (const id of ids) {
                  const el = document.getElementById(id);
                  if (el) out.push(el);
                }
                for (const sel of [
                  '[data-testid="modal-content"]',
                  '[data-testid="modal"]',
                  ".client-book-session-modal",
                ]) {
                  document.querySelectorAll(sel).forEach((n) => out.push(n));
                }
                out.push(document.body);
                return out;
              }

              function findButton() {
                const direct = document.querySelector(
                  '.client-book-session-modal [data-testid="primaryButton"],'
                  + ' #modal-content [data-testid="primaryButton"],'
                  + ' #modal [data-testid="primaryButton"]'
                );
                if (
                  direct &&
                  String(direct.tagName).toUpperCase() === "BUTTON" &&
                  !direct.disabled
                ) {
                  return direct;
                }
                for (const root of roots()) {
                  const b = root.querySelector('[data-testid="primaryButton"]');
                  if (b && String(b.tagName).toUpperCase() === "BUTTON" && !b.disabled) return b;
                }
                const buttons = Array.from(document.querySelectorAll("button"));
                for (const b of buttons) {
                  const t = norm(b.innerText) || norm(b.textContent);
                  if (
                    t === "add appointment" ||
                    /^add\\s+appointment$/i.test(t) ||
                    t.indexOf("add appointment") >= 0
                  ) {
                    return b;
                  }
                }
                return null;
              }

              function fire(el) {
                el.scrollIntoView({ block: "center", inline: "center", behavior: "instant" });
                const r = el.getBoundingClientRect();
                const x = r.left + Math.min(r.width / 2, 80);
                const y = r.top + Math.min(r.height / 2, 40);
                const base = {
                  bubbles: true,
                  cancelable: true,
                  clientX: x,
                  clientY: y,
                  view: window,
                };
                try {
                  el.dispatchEvent(
                    new PointerEvent("pointerdown", {
                      ...base,
                      pointerId: 1,
                      pointerType: "mouse",
                      isPrimary: true,
                    })
                  );
                } catch (e) {}
                el.dispatchEvent(new MouseEvent("mousedown", base));
                try {
                  el.dispatchEvent(
                    new PointerEvent("pointerup", {
                      ...base,
                      pointerId: 1,
                      pointerType: "mouse",
                      isPrimary: true,
                    })
                  );
                } catch (e) {}
                el.dispatchEvent(new MouseEvent("mouseup", base));
                el.dispatchEvent(new MouseEvent("click", base));
                if (typeof el.click === "function") el.click();
              }

              const btn = findButton();
              if (!btn) return { ok: false, reason: "no_button" };
              if (btn.disabled) return { ok: false, reason: "disabled" };
              const gap = __GAP_MS__;
              fire(btn);
              await new Promise((r) => setTimeout(r, gap));
              fire(btn);
              const form = btn.closest("form");
              if (form && typeof form.requestSubmit === "function") {
                try {
                  form.requestSubmit(btn);
                } catch (e) {}
              }
              return { ok: true, tag: String(btn.tagName) };
            }
        """
        result = await page.evaluate(_dom_script.strip().replace("__GAP_MS__", _gap))
        if isinstance(result, dict) and result.get("ok"):
            return True
        return False
    except Exception:
        return False


async def _click_healthie_modal_submit(modal, page: Page) -> bool:
    """Click **Add appointment** (`primaryButton`).

    Uses **in-page DOM** activation (synthetic events + ``requestSubmit``), then Playwright,
    then a second DOM pass after scrolling again.
    """
    await _scroll_healthie_booking_modal_to_bottom(modal)
    await page.wait_for_timeout(200)

    await _click_add_appointment_via_dom(page)
    await page.wait_for_timeout(250)

    candidates = [
        modal.get_by_test_id("primaryButton").first,
        page.locator('[data-testid="modal-content"] [data-testid="primaryButton"]').first,
    ]

    async def _try_playwright_click(loc) -> bool:
        try:
            await loc.wait_for(state="attached", timeout=6000)
        except Exception:
            return False
        try:
            await loc.scroll_into_view_if_needed(timeout=5000)
        except Exception:
            pass
        await page.wait_for_timeout(120)
        try:
            await loc.click(timeout=15000, force=True)
            await page.wait_for_timeout(_ADD_APPOINTMENT_DOUBLE_GAP_MS)
            await loc.click(timeout=15000, force=True)
            return True
        except Exception:
            pass
        try:
            handle = await loc.element_handle()
            if handle:
                _h = """async (el) => {
                      const go = () => {
                        el.scrollIntoView({ block: 'center', behavior: 'instant' });
                        el.click();
                      };
                      const gap = __GAP_MS__;
                      go();
                      await new Promise((r) => setTimeout(r, gap));
                      go();
                    }"""
                await handle.evaluate(_h.replace("__GAP_MS__", str(_ADD_APPOINTMENT_DOUBLE_GAP_MS)))
                return True
        except Exception:
            pass
        return False

    for loc in candidates:
        if await _try_playwright_click(loc):
            return True

    _legacy_click = """
            async () => {
              const btn = document.querySelector(
                '[data-testid="modal-content"] [data-testid="primaryButton"],'
                + ' [data-testid="modal"] [data-testid="primaryButton"]'
              );
              if (!btn || btn.disabled) return false;
              const gap = __GAP_MS__;
              const once = () => {
                btn.scrollIntoView({ block: 'center', behavior: 'instant' });
                btn.click();
              };
              once();
              await new Promise((r) => setTimeout(r, gap));
              once();
              return true;
            }
        """
    try:
        ok = await page.evaluate(
            _legacy_click.strip().replace("__GAP_MS__", str(_ADD_APPOINTMENT_DOUBLE_GAP_MS))
        )
        if ok:
            return True
    except Exception:
        pass

    await _scroll_healthie_booking_modal_to_bottom(modal)
    await page.wait_for_timeout(200)
    if await _click_add_appointment_via_dom(page):
        await page.wait_for_timeout(300)
        return True

    return False


async def create_appointment(
    patient_id: str,
    date: str,
    time: str,
    appointment_type: str | None = None,
) -> dict | None:
    """Create an appointment in Healthie for the specified patient.

    Opens the **Book session** modal from the client page. Navigating to
    ``/clients/{id}/appointments/new`` is avoided: the SPA treats ``new`` as a
    client search keyword and shows an empty list.

    Args:
        patient_id: The unique identifier for the patient in Healthie.
        date: The desired appointment date in a format that Healthie accepts.
        time: The desired appointment time in a format that Healthie accepts.
        appointment_type: ``initial_consultation`` / ``follow_up`` (or phrases like
            "initial consultation"); selects the matching Healthie visit type.

    Returns:
        dict | None: A dictionary containing appointment information if created
            successfully, including at least an 'appointment_id' field.
            Returns None if appointment creation fails.

    Example return value:
        {
            "appointment_id": "67890",
            "patient_id": "12345",
            "date": "2026-02-15",
            "time": "10:00 AM",
            ...
        }
    """
    page = await login_to_healthie()
    normalized_date = _normalize_date(date)
    display_date = _healthie_modal_date_display(normalized_date, date)

    try:
        await page.goto(
            f"https://secure.gethealthie.com/clients/{patient_id}",
            wait_until="domcontentloaded",
        )
        try:
            await page.wait_for_load_state("load", timeout=20000)
        except Exception:
            pass
        await _ensure_page_hydrated(page, timeout_ms=25000, reason="appointment_client_not_hydrated")
        # #root can contain only the spinner; _ensure_page_hydrated returns too early. Wait it out.
        try:
            await _wait_until_clients_page_ready(page, timeout_ms=45000)
        except Exception as exc:
            logger.warning("Loading overlay wait on client profile: {}", exc)
        await page.wait_for_timeout(800)

        if not await _open_book_session_modal(page, patient_id):
            logger.error("Could not open Book session for patient {}", patient_id)
            return None

        modal = page.locator('[data-testid="modal-content"]').first
        try:
            await modal.wait_for(state="visible", timeout=15000)
        except Exception:
            logger.error("Booking modal did not appear")
            return None

        if not await _select_healthie_appointment_type(page, modal, appointment_type):
            logger.error("Could not set Appointment type in booking modal")
            return None

        filled_date, filled_time = await _fill_healthie_booking_datetime(modal, page, display_date, time)

        if not filled_date or not filled_time:
            logger.error("Could not fill booking modal date/time fields")
            return None

        await _dismiss_healthie_datepicker_overlays(page)
        await page.wait_for_timeout(500)

        if not await _click_healthie_modal_submit(modal, page):
            await _scroll_healthie_booking_modal_to_bottom(modal)
            clicked = False
            for txt in ("Add appointment",):
                loc = modal.locator(f'button:has-text("{txt}")').first
                try:
                    await loc.wait_for(state="attached", timeout=2000)
                    await loc.scroll_into_view_if_needed()
                    try:
                        await loc.click(timeout=8000, force=True)
                        if txt == "Add appointment":
                            await page.wait_for_timeout(_ADD_APPOINTMENT_DOUBLE_GAP_MS)
                            await loc.click(timeout=8000, force=True)
                        clicked = True
                    except Exception:
                        h = await loc.element_handle()
                        if h:
                            _fh = """async (el) => {
                              const go = () => {
                                el.scrollIntoView({ block: 'center', behavior: 'instant' });
                                el.click();
                              };
                              const gap = __GAP_MS__;
                              go();
                              await new Promise((r) => setTimeout(r, gap));
                              if (__DOUBLE__) { go(); }
                            }"""
                            await h.evaluate(
                                _fh.strip()
                                .replace("__GAP_MS__", str(_ADD_APPOINTMENT_DOUBLE_GAP_MS))
                                .replace(
                                    "__DOUBLE__",
                                    "true" if txt == "Add appointment" else "false",
                                )
                            )
                            clicked = True
                    if clicked:
                        break
                except Exception:
                    continue
            if not clicked:
                logger.error("Could not click Add appointment / submit in booking modal")
                return None

        await page.wait_for_timeout(2500)

        current_url = page.url
        appointment_id = None
        if "/appointments/" in current_url:
            appointment_id = current_url.rstrip("/").split("/")[-1]

        body = (await page.inner_text("body")).lower()
        if any(
            success in body
            for success in (
                "appointment",
                "scheduled",
                "created",
                "confirmed",
            )
        ):
            logger.info("Appointment created for patient {}", patient_id)
            return {
                "appointment_id": appointment_id or "unknown",
                "patient_id": patient_id,
                "date": normalized_date or date,
                "time": time,
                "status": "scheduled",
            }

        logger.error("Could not verify appointment creation")
        return None
    except Exception as exc:
        logger.exception("Failed to create appointment: {}", exc)
        return None


def _normalize_date(raw: str) -> str:
    """Normalize date strings to YYYY-MM-DD when possible."""
    value = raw.strip()
    if not value:
        return value

    fmts = ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d %Y", "%b %d %Y")
    for fmt in fmts:
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value


def _healthie_modal_date_display(normalized_iso: str | None, raw: str) -> str:
    """Format date like 'April 26, 2026' for Healthie booking modal fields."""
    for cand in (normalized_iso, _normalize_date(raw), raw.strip()):
        if not cand:
            continue
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%B %d, %Y", "%b %d, %Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(cand, fmt).strftime("%B %d, %Y")
            except ValueError:
                continue
    return raw.strip()


def _date_variants(raw: str) -> list[str]:
    """Generate common DOB textual variants for on-page matching."""
    normalized = _normalize_date(raw)
    variants = {normalized.lower(), raw.strip().lower()}
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%d")
        variants.update(
            {
                parsed.strftime("%Y-%m-%d").lower(),
                parsed.strftime("%m/%d/%Y").lower(),
                parsed.strftime("%m-%d-%Y").lower(),
                f"{parsed.month}/{parsed.day}/{parsed.year}".lower(),
                parsed.strftime("%d/%m/%Y").lower(),
                parsed.strftime("%d-%m-%Y").lower(),
                parsed.strftime("%B %d, %Y").lower(),
                parsed.strftime("%b %d, %Y").lower(),
                parsed.strftime("%d %B %Y").lower(),
                parsed.strftime("%d %b %Y").lower(),
            }
        )
        sm = calendar.month_name[parsed.month].lower()
        sa = calendar.month_abbr[parsed.month].lower()
        variants.add(f"{parsed.day} {sm} {parsed.year}".lower())
        variants.add(f"{parsed.day} {sa} {parsed.year}".lower())
    except ValueError:
        pass
    return [v for v in variants if v]


def _profile_body_normalized(raw: str) -> str:
    t = raw.lower().replace("\u00a0", " ").replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(t.split())


def _dob_matches_profile_body(body: str, dob_variants: list[str], normalized_iso: str | None) -> bool:
    """True if DOB appears in profile text (substring, flexible m/d/y, or digit patterns)."""
    if any(v and v in body for v in dob_variants):
        return True
    if not normalized_iso:
        return False
    try:
        dt = datetime.strptime(normalized_iso, "%Y-%m-%d")
    except ValueError:
        return False
    y, m, d = dt.year, dt.month, dt.day
    if str(y) not in body:
        return False
    patterns = (
        rf"\b0*{m}[-/.]0*{d}[-/.]{y}\b",
        rf"\b0*{d}[-/.]0*{m}[-/.]{y}\b",
        rf"\b{y}[-/.]0*{m:02d}[-/.]0*{d:02d}\b",
        rf"\b{y}[-/.]0*{m}[-/.]0*{d}\b",
    )
    return any(re.search(p, body) for p in patterns)


def _extract_client_id(href: str) -> str | None:
    """Extract numeric id from Healthie profile URLs (/clients/ or /users/ on clients list)."""
    if not href:
        return None
    cleaned = href.replace("https://secure.gethealthie.com", "").replace("https://app.gethealthie.com", "")
    for pattern in (
        r"/clients/(\d+)(?:$|[/?#\"'&])",
        r"/users/(\d+)(?:$|[/?#\"'&])",
    ):
        match = re.search(pattern, cleaned, re.I)
        if match:
            return match.group(1)
    return None


def _extract_client_ids_from_html(html: str) -> list[str]:
    """Find numeric ids in row markup: /clients/, /users/, user_id-*, book-session-with-*."""
    h = html or ""
    ids: list[str] = []
    ids += re.findall(r"/(?:clients|users)/(\d+)(?:[\"'/?#&\s]|$)", h, flags=re.I)
    ids += re.findall(r"user_id-(\d+)", h, flags=re.I)
    ids += re.findall(r"book-session-with-(\d+)", h, flags=re.I)
    return list(dict.fromkeys(ids))


async def _candidates_from_table_row_for_name(page: Page, patient_name: str) -> list[dict]:
    """Read profile id from the filtered table row (Healthie uses /users/<id> on client-link anchors)."""
    row = page.get_by_role("row").filter(has_text=patient_name).filter(has_text="Book session").first
    try:
        await row.wait_for(state="visible", timeout=6000)
    except Exception:
        row = page.get_by_role("row").filter(has_text=patient_name).first
        try:
            await row.wait_for(state="visible", timeout=4000)
        except Exception:
            return []

    try:
        ctx = (await row.inner_text())[:500]
    except Exception:
        ctx = ""

    out: list[dict] = []
    seen: set[str] = set()
    try:
        n = await row.locator("a[href]").count()
        for i in range(n):
            href = await row.locator("a[href]").nth(i).get_attribute("href") or ""
            pid = _extract_client_id(href)
            if not pid or pid in seen:
                continue
            seen.add(pid)
            text = (await row.locator("a[href]").nth(i).inner_text()).strip()
            out.append(
                {
                    "id": pid,
                    "href": href,
                    "text": text or patient_name,
                    "context": ctx,
                }
            )
    except Exception:
        pass

    if not out:
        try:
            html = await row.evaluate("el => el.innerHTML")
            for pid in _extract_client_ids_from_html(html):
                if pid in seen:
                    continue
                seen.add(pid)
                href = f"/users/{pid}" if f"/users/{pid}" in html else f"/clients/{pid}"
                out.append(
                    {
                        "id": pid,
                        "href": href,
                        "text": patient_name,
                        "context": ctx,
                    }
                )
        except Exception:
            pass

    return out


async def _collect_client_candidates(page: Page, patient_name: str) -> list[dict]:
    """Merge global link scan with table-row extraction (filtered list often only has row-local markup)."""
    from_dom = await _client_profile_candidates_from_dom(page)
    from_row = await _candidates_from_table_row_for_name(page, patient_name)
    merged: dict[str, dict] = {}
    for c in from_dom + from_row:
        pid = c.get("id")
        if not pid:
            continue
        if pid not in merged or len((c.get("context") or "")) > len((merged[pid].get("context") or "")):
            merged[pid] = c
    return list(merged.values())


async def _client_profile_candidates_from_dom(page: Page) -> list[dict]:
    """Collect profile links for numeric ids (/clients/ and /users/ — Healthie lists use /users/).

    Autocomplete View Profile links may still use /clients/; table name links often use /users/.
    """
    return await page.evaluate(
        r"""
        () => {
          const re = /\/(clients|users)\/(\d+)(?:$|[?#/])/i;
          const out = [];
          const seen = new Set();
          for (const el of document.querySelectorAll('a[href]')) {
            const raw = el.getAttribute('href') || '';
            if (!raw.includes('/clients/') && !raw.includes('/users/')) continue;
            const abs = raw.startsWith('http') ? raw : (window.location.origin + (raw.startsWith('/') ? raw : '/' + raw));
            const m = abs.match(re);
            if (!m) continue;
            const id = m[2];
            if (seen.has(id)) continue;
            seen.add(id);
            let ctx = '';
            let p = el;
            for (let i = 0; i < 8 && p; i++) {
              ctx = (p.innerText || '').trim() + '\n' + ctx;
              p = p.parentElement;
            }
            out.push({
              id,
              href: raw,
              text: (el.innerText || '').trim().slice(0, 200),
              context: ctx.slice(0, 500),
            });
          }
          return out;
        }
        """
    )


def _absolute_healthie_href(href: str) -> str:
    if href.startswith("http"):
        return href
    path = href if href.startswith("/") else f"/{href}"
    return f"https://secure.gethealthie.com{path}"


async def _healthie_visible_login_field(
    page: Page, selectors: list[str], purpose: str, *, timeout_ms: int
) -> Locator:
    """Return the first **visible** input matching any selector (Frontegg varies type vs name)."""
    deadline = time.monotonic() + timeout_ms / 1000.0
    last: BaseException | None = None
    while time.monotonic() < deadline:
        for sel in selectors:
            loc = page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=600)
                return loc
            except Exception as exc:
                last = exc
                continue
        await page.wait_for_timeout(150)
    err = Exception(f"Could not find visible {purpose} field on {page.url}")
    if isinstance(last, BaseException):
        raise err from last
    raise err


async def _submit_email_step(page: Page) -> None:
    """Frontegg-style step: continue after email before password field appears."""
    for label in ("Continue", "Next"):
        btn = page.get_by_role("button", name=label).first
        try:
            if await btn.is_visible(timeout=1500):
                await btn.click()
                await page.wait_for_timeout(700)
                return
        except Exception:
            continue
    try:
        email_loc = await _healthie_visible_login_field(
            page,
            [
                'input[type="email"]',
                'input[name="email"]',
                'input[name="Email"]',
                'input[autocomplete="email"]',
            ],
            "email",
            timeout_ms=5000,
        )
        await email_loc.press("Enter")
    except Exception:
        pass
    await page.wait_for_timeout(700)


async def _wait_for_login_form(page: Page, timeout_ms: int = 30000) -> None:
    """Wait until Frontegg shows an email or password field (``type=email`` alone is not enough)."""
    await _healthie_visible_login_field(
        page,
        [
            'input[type="email"]',
            'input[name="email"]',
            'input[name="Email"]',
            'input[autocomplete="email"]',
            'input[autocomplete="username"]',
            'input[type="password"]',
        ],
        "login",
        timeout_ms=timeout_ms,
    )


def _graphql_search_response_predicate(response) -> bool:
    req = response.request
    return (
        "gethealthie.com/graphql" in response.url
        and req.method == "POST"
        and response.ok
    )


async def _react_set_search_value(search_input, value: str) -> None:
    """Set value and notify React/listeners (fill alone often does not trigger client search)."""
    await search_input.click()
    await search_input.fill("")
    await search_input.fill(value)
    await search_input.evaluate(
        """el => {
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }"""
    )


async def _submit_clients_list_search(page: Page, search_input, query: str) -> None:
    """Drive client search: debounced typing; avoid Enter when the filtered table already shows results.

    Enter can clear the filter or break the SPA state after Healthie has already narrowed to one row.
    """
    await _react_set_search_value(search_input, query)
    await page.wait_for_timeout(2000)
    cand = await _client_profile_candidates_from_dom(page)
    if len(cand) >= 1:
        return
    try:
        row = page.get_by_role("row").filter(has_text=query).filter(has_text="Book session").first
        if await row.is_visible(timeout=800):
            return
    except Exception:
        pass
    try:
        if await page.locator(r"text=/Active clients\s*:\s*[1-9]/").first.is_visible(timeout=500):
            body = (await page.inner_text("body")).lower()
            if query.lower() in body:
                return
    except Exception:
        pass
    try:
        async with page.expect_response(_graphql_search_response_predicate, timeout=8000):
            await search_input.press("Enter")
    except Exception:
        pass
    await page.wait_for_timeout(700)


def _filter_candidates_by_name(candidates: list[dict], name: str) -> list[dict]:
    """Prefer links whose row or autocomplete panel mentions the patient (not just link label).

    Dropdown uses anchors like 'View Profile'; name and DOB live in parent context.
    """
    if not candidates:
        return []
    nl = " ".join(name.lower().split())
    parts = [p for p in nl.split() if len(p) > 1]
    rows: list[dict] = []
    for c in candidates:
        blob = ((c.get("context") or "") + " " + (c.get("text") or "")).lower().strip()
        if not blob:
            continue
        if nl in blob or (parts and all(p in blob for p in parts)):
            rows.append(c)
    return rows if rows else candidates


def _score_client_candidate(c: dict, name: str, dob_variants: list[str]) -> int:
    """Higher score = better match for autocomplete + table rows."""
    blob = ((c.get("context") or "") + " " + (c.get("text") or "")).lower()
    nl = " ".join(name.lower().split())
    score = 0
    if nl in blob:
        score += 20
    for p in nl.split():
        if len(p) > 2 and p in blob:
            score += 5
    for dv in dob_variants:
        if dv and dv in blob:
            score += 30
    return score


def _sort_candidates_by_match(candidates: list[dict], name: str, dob_variants: list[str]) -> list[dict]:
    return sorted(
        candidates,
        key=lambda c: (-_score_client_candidate(c, name, dob_variants), c.get("id", "")),
    )


async def _fallback_search_by_keystrokes(page: Page, search_input, query: str) -> None:
    """If filter still empty, re-enter query slowly (some controlled inputs ignore fill())."""
    await search_input.click()
    await search_input.fill("")
    await search_input.press_sequentially(query, delay=45)
    await search_input.evaluate(
        """el => {
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
        }"""
    )
    await page.wait_for_timeout(2000)
    cand = await _client_profile_candidates_from_dom(page)
    if len(cand) >= 1:
        return
    try:
        row = page.get_by_role("row").filter(has_text=query).filter(has_text="Book session").first
        if await row.is_visible(timeout=600):
            return
    except Exception:
        pass
    try:
        async with page.expect_response(_graphql_search_response_predicate, timeout=8000):
            await search_input.press("Enter")
    except Exception:
        pass
    await page.wait_for_timeout(800)


async def _wait_for_clients_search_input(
    page: Page,
    timeout_ms: int = 45000,
):
    """Wait for the clients table search field (Healthie: ``form[role=search]`` in main)."""
    ordered = [
        '[role="main"] form[role="search"] input',
        'form[role="search"] input',
        '[role="main"] input[placeholder*="Search"]',
    ]

    for attempt in range(2):
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            for sel in ordered:
                remaining_ms = int((deadline - time.monotonic()) * 1000)
                if remaining_ms < 400:
                    break
                loc = page.locator(sel).first
                try:
                    await loc.wait_for(
                        state="visible",
                        timeout=min(8000, remaining_ms),
                    )
                    return loc
                except Exception:
                    continue
            await page.wait_for_timeout(350)
        if attempt == 0:
            logger.warning("Reloading /clients and retrying search input wait")
            await page.reload(wait_until="domcontentloaded")
            try:
                await page.wait_for_load_state("load", timeout=15000)
            except Exception:
                pass
            await _ensure_page_hydrated(page, timeout_ms=25000, reason="clients_reload_not_hydrated")
            await _wait_until_clients_page_ready(page, timeout_ms=20000)

    return None


async def _wait_until_clients_page_ready(page: Page, timeout_ms: int = 25000) -> None:
    """Wait until Healthie's full-page spinner (#loading-state-container) is gone.

    Used on /clients list and on /clients/{id} profile — both show the same overlay while hydrating.
    """
    elapsed = 0
    step_ms = 500
    while elapsed < timeout_ms:
        loading_indicator = page.locator("#loading-state-container")
        try:
            visible = await loading_indicator.is_visible(timeout=200)
        except Exception:
            visible = False
        if not visible:
            return
        await page.wait_for_timeout(step_ms)
        elapsed += step_ms

    raise Exception(f"Clients page did not finish loading within {timeout_ms}ms (url={page.url})")


async def _wait_for_client_results(
    page: Page, timeout_ms: int = 25000, patient_name: str | None = None
) -> None:
    """Wait for autocomplete, table row, or client links after search."""
    elapsed = 0
    step_ms = 350
    while elapsed < timeout_ms:
        cand = await _client_profile_candidates_from_dom(page)
        if len(cand) >= 1:
            return
        if patient_name:
            try:
                r = page.get_by_role("row").filter(has_text=patient_name).filter(has_text="Book session").first
                if await r.is_visible(timeout=200):
                    return
            except Exception:
                pass
            try:
                r2 = page.get_by_role("row").filter(has_text=patient_name).first
                if await r2.is_visible(timeout=200):
                    return
            except Exception:
                pass
            try:
                if await page.locator(r"text=/Active clients\s*:\s*[1-9]/").first.is_visible(timeout=200):
                    body = (await page.inner_text("body")).lower()
                    if patient_name.lower() in body:
                        return
            except Exception:
                pass
        try:
            vp = page.get_by_role("link", name="View Profile").first
            if await vp.is_visible(timeout=200):
                return
        except Exception:
            pass
        try:
            opt = page.locator('[role="listbox"] [role="option"], [role="menu"] [role="menuitem"]').first
            if await opt.is_visible(timeout=200):
                return
        except Exception:
            pass
        await page.wait_for_timeout(step_ms)
        elapsed += step_ms


async def _ensure_page_hydrated(
    page: Page, timeout_ms: int = 30000, reason: str = "page_not_hydrated"
) -> None:
    """Ensure SPA JS has hydrated root content; retry once with reload if needed."""
    for attempt in range(2):
        elapsed = 0
        step_ms = 500
        while elapsed < timeout_ms:
            is_hydrated = await page.evaluate(
                """
                () => {
                  const root = document.querySelector("#root");
                  if (!root) return false;
                  return root.children.length > 0 || (root.textContent || "").trim().length > 0;
                }
                """
            )
            if is_hydrated:
                return
            await page.wait_for_timeout(step_ms)
            elapsed += step_ms

        if attempt == 0:
            logger.warning("SPA not hydrated yet on {}, retrying page reload", page.url)
            await page.reload(wait_until="domcontentloaded")

    raise Exception(f"SPA did not hydrate after retries (url={page.url})")


