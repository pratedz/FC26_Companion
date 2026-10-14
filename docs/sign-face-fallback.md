# Sign fallback behavior

Cards in the Sign surface now expose one of four face states:

- **Verified face** uses the exact FC26 base-player head values.
- **Safe generic face** uses the card player's native FC26 generic appearance.
- **Generic fallback** uses a same-gender generic FC26 template when the FC26 base row is absent.
- **No usable face** is blocked and explains that the base-player templates need repair.

Generic and fallback cards can be added and reviewed. The review explicitly says that a generic face does not reproduce the player's real likeness. The **Real faces only** filter remains available for users who want to exclude generic appearances.

All generic plans clear real-head asset fields (`headassetid`, `headclasscode`, and high-quality-head flags), so an FC27 item ID is never sent as a face asset. The reviewed face plan is part of the checkout signature; if it changes before queueing, the app requires another review.
