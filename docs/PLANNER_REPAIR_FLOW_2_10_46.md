# Plan these players repair flow — 2.10.46

## Cause

The stored Club snapshot had goalkeeper PlayStyle+ on two outfield players:
Cristiano Ronaldo (85308) had GK Far Reach+; Virgil van Dijk (80131) had
GK Deflector+. Unlimited additive improvement preserved these existing bits,
then rejected the completed proposal for being incompatible with the position.
Rebuild repeated the same merge, so another valid AI proposal could fail again.
This establishes the rebuild failure mechanism; it does not establish which
earlier operation originally wrote those incompatible values.

The numbered request also exposed a separate parser error: `playstyles+ 3.
arrange ...` was treated as a requested count of three, suppressing the richer
unlimited improvement defaults.

## New behavior

- Build removes inherited position-incompatible PlayStyles and PlayStyle+ when
  styles are requested. The preview names the removed styles and shows removals.
- Regular Career/CPU traits remain valid. Invalid storage values and invalid or
  missing AI proposals still receive validation errors.
- With an unchanged request, an incomplete build offers **Repair affected players**.
  It invalidates cached proposals for blocked players and keeps valid teammates.
- Selecting a blocked player offers **Repair this player**, which explicitly
  invalidates that player's proposal. Other unresolved missing proposals may
  still need generation; they are not silently accepted.
- Changing the request, selection, roles or targets instead requires **Rebuild**.
  Individual repair is hidden until the changed request has been rebuilt.
- All repairs return to preview. Apply remains unavailable until required player
  changes validate; repair does not write directly to the Career.
- Numbered checklist items no longer count as explicit PlayStyle quantities.
  Explicit quantities remain respected. Unlimited improvement defaults are
  minimums, with no upper cap on position-compatible styles.
- The four-part skills/styles/shirts request does not authorize rating,
  potential, attribute, age, body or identity changes.

## Verification

119 focused tests passed; two GUI tests skipped under the existing headless
test policy. New coverage reproduces the inherited Ronaldo/Van Dijk conflicts
in a 20-player proposal, including an incompatible goalkeeper, under standard
and unlimited modes. It checks valid preview/apply jobs, distinct shirt numbers,
five-star skills, outfield Quick Step+, preserved regular Career bits, rich
unlimited defaults, no unrequested attribute writes, targeted cache invalidation,
and individual repair button behavior without opening windows.

These are automated mocked-provider and transport checks, not a live Career
apply. The in-game worker remains 2.6.29; this update changes Companion only.
