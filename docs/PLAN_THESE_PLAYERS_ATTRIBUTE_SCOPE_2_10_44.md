# Plan These Players attribute scope — 2.10.44

Feature-only requests such as skill moves, PlayStyles, and recommended shirts no longer imply a squad-wide attribute rebuild. Ratings and attributes are planned only when the request names a rating, stat, attribute, or overall target.

The AI field contract omits ratings and attributes for feature-only requests. The local staging boundary filters every proposed field against the requested families and does not synthesize overall or potential values when attributes were not requested. The squad cohesion step also skips those requests, so it cannot inject an overall change after sanitization.

The Companion app was rebuilt as 2.10.44. This is an app-only change; the resident worker version and Career save are unchanged.

Validation: packaged desktop build and root/portable bundle installation. Automated test suites were not run for this change.
