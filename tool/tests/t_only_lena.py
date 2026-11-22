"""20 Nov (Lena): "all of the wahtsapp is jsut for lena chcek nobody else has anything of same ffeatrs or even naem
there" and "if autoread is off just do it plainly". A real window, parked off-screen and invisible, on the pinned roster
with the stand-in script (greenrig) - nothing reaches the live roster or WhatsApp.
- another controller's log shows nothing of WhatsApp: no button, no banner, no read controls, not the word anywhere;
- Lena's log has the WhatsApp button back;
- the window opens with Auto read off, and then the log is plain: the generated Ok, no reply hints, and Auto green
  greens a generated Ok as it did before WhatsApp; with Auto read on, a generated Ok needs a real reply first."""
import sys

import greenrig as g

FRI = "2026-10-23"
whatsapp = g.calllog.whatsapp
total = []


def words_on_screen(root):
    """Every piece of text on a mapped widget of the window: labels, buttons, canvas text, text boxes."""
    found, stack = [], [root]
    while stack:
        w = stack.pop()
        stack.extend(w.winfo_children())
        try:
            if not w.winfo_ismapped():
                continue
        except Exception:
            continue
        texts = []
        if isinstance(getattr(w, "text", None), str):
            texts.append(w.text)
        try:
            texts.append(str(w.cget("text")))
        except Exception:
            pass
        try:
            if w.winfo_class() == "Canvas":
                texts += [w.itemcget(i, "text") for i in w.find_all() if w.type(i) == "text"]
            elif w.winfo_class() == "Text":
                texts.append(w.get("1.0", "end"))
        except Exception:
            pass
        found += [t for t in texts if t and t.strip()]
    return found


def mentions(rig, *needles):
    rig.pump(150)
    return [t for t in words_on_screen(rig.root) if any(n.lower() in t.lower() for n in needles)]


rig = g.rig_for("only-lena", "2026-10-23 20:06")
rig.check("the window opens with Auto read off, so the logs use no WhatsApp replies", not whatsapp.replies_on())
rig.show("Soren Rask", FRI)
button = rig.app.buttons.get("whatsapp")
beta = rig.app.beta
rig.check("Soren's log: no WhatsApp button beside Fill in", button is None or not button.winfo_manager(),
          button and button.winfo_manager())
rig.check("...no WhatsApp banner or read controls", beta.banner is None or not beta.banner.winfo_manager())
seen = mentions(rig, "whatsapp", "auto read", "full check", "reply not read", "drafts", "sending:")
rig.check("...and not the word WhatsApp (or any of its controls) anywhere on the window", not seen, seen[:5])
whatsapp.set_replies(True)                          # (even with Auto read on for Lena, Soren's log stays plain)
rig.app.rebuild()
rig.app.refresh()
seen = mentions(rig, "whatsapp", "auto read", "full check", "reply not read")
rig.check("...even with Auto read on: nothing WhatsApp on Soren's log", not seen and not rig.app.wa_says(), seen[:5])
made = [c for c in rig.app.pack["calls"]["calls"] if c["call_text"] and not c["ahead"]]
rig.check("...and his made calls keep the plain generated Ok", made and all(c["response"] == "Ok" and not c["heard"] for c in made),
          [c["response"] for c in made][:6])
whatsapp.set_replies(False)

rig.show("Lena Drayton", FRI)
button = rig.app.buttons.get("whatsapp")
rig.check("Lena's log: the WhatsApp button is back beside Fill in", button is not None and button.winfo_manager() == "pack")
seen = mentions(rig, "whatsapp")
rig.check("...and the screen scan that found nothing on Soren's does find it here (the scan can see)", bool(seen), seen[:3])
rig.check("...but with Auto read off nothing on the bottom line speaks for WhatsApp", not rig.app.wa_says())
log = rig.app.pack["calls"]
candidate = next(c for c in log["calls"] if c["call_text"] and not c["ahead"] and not c.get("strike")
                 and c["response"] == "Ok" and not c.get("heard") and not whatsapp.trusted(c["name"])
                 and g.rostercolour.css(c.get("fill")) == g.rostercolour.WHITE)
hints = [c["name"] for c in log["calls"] if whatsapp.cell_hint(c)]
rig.check("Auto read off: no reply hints in Lena's table either", not hints, hints[:5])


def due():
    rig.app.auto_for.add("Lena Drayton")
    try:
        return any(c["ref"] == candidate["ref"] and action == "green" for c, action, _, _ in rig.app.auto_due())
    finally:
        rig.app.auto_for.discard("Lena Drayton")


rig.check("Auto read off: Auto green greens a generated Ok, as before WhatsApp", due())
whatsapp.set_replies(True)
rig.app.rebuild()
rig.app.refresh()
rig.check("Auto read on: the same generated Ok waits for a real reply first", not due())
rig.check("...and the bottom line may speak for WhatsApp again, on Lena's log only", rig.app.wa_says())
whatsapp.set_replies(False)
rig.check("nothing was sent to the roster script", not rig.fake.sent, rig.fake.sent)
rig.check("no error inside the window", not rig.errors, rig.errors[:1])
total += rig.close()

print("\n%d of %d checks passed" % (sum(total), len(total)))
sys.exit(0 if total and all(total) else 1)
