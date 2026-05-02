# SW3P Test Schedule

**Version:** 1.0 · **Date:** 2026-05-02 · **Baseline:** 289 tests passing  
**Repo:** https://github.com/JcbBnd26/swpppautofill\_windows

\---

## test\_auth.py — Auth, users, sessions, invites, passwords (104 tests)

|Test|Description|
|-|-|
|init\_db\_creates\_tables|Database tables exist after init|
|seed\_app\_idempotent|Seeding an app twice doesn't duplicate it|
|invite\_code\_format|Invite codes follow TOOLS-XXXX-XXXX format|
|claim\_valid\_code|Valid invite code creates a user and session|
|claim\_invalid\_code|Bogus code returns failure|
|claim\_already\_claimed|Claimed code can't be claimed again|
|claim\_admin\_invite\_grants\_admin|Admin invite flag propagates to user|
|claim\_case\_insensitive|Code claim works regardless of letter case|
|me\_unauthenticated|/me returns 401 without a session|
|admin\_unauthenticated|Admin endpoint returns 401 without session|
|admin\_non\_admin\_user|Non-admin user can't reach admin endpoints|
|me\_returns\_user\_info|/me returns correct user data|
|logout\_clears\_session|Logout kills the server-side session|
|list\_users|Admin can list all users|
|deactivate\_user|Admin can deactivate a user|
|cannot\_deactivate\_self|Admin can't deactivate their own account|
|create\_and\_list\_invites|Admin can create and list invite codes|
|revoke\_invite|Admin can revoke a pending invite|
|revoke\_already\_claimed|Revoking a claimed invite returns failure|
|create\_invite\_validates\_apps|Invite creation rejects invalid app IDs|
|list\_and\_kill\_sessions|Admin can list and delete user sessions|
|kill\_session\_by\_prefix|Session can be deleted by token prefix|
|grant\_and\_revoke\_app|App access can be granted and revoked per user|
|list\_apps|Admin can list all registered apps|
|create\_app|Admin can register a new app|
|create\_app\_validates\_id|App ID format is enforced|
|update\_app|Admin can update app fields|
|claim\_whitespace\_padded\_code|Whitespace around invite code is trimmed|
|claim\_empty\_code|Empty code returns failure|
|claim\_very\_long\_code|Oversized code returns failure|
|create\_invite\_empty\_apps|Invite with no app permissions is allowed|
|revoke\_already\_revoked|Revoking an already-revoked code is safe|
|revoke\_nonexistent\_code|Revoking a nonexistent code doesn't crash|
|create\_invite\_very\_long\_name|Invite name over max length is rejected|
|set\_and\_login\_with\_password|User can set password and log in with it|
|login\_wrong\_password|Wrong password returns failure|
|login\_nonexistent\_user|Login for unknown user returns failure|
|login\_no\_password\_set|Login fails if user has no password set|
|login\_case\_insensitive\_name|Login username match is case-insensitive|
|set\_password\_requires\_auth|Setting password requires active session|
|set\_password\_too\_short|Password under 8 chars is rejected|
|change\_password|User can change their own password|
|claim\_sets\_fresh\_cookie\_not\_overwritten|Claim flow sets session cookie correctly|
|signin\_sets\_fresh\_cookie\_not\_overwritten|Login sets session cookie correctly|
|existing\_session\_gets\_refreshed|Active session extends expiry on each request|
|init\_db\_creates\_password\_hash\_column|Migration adds password\_hash column|
|init\_db\_idempotent|Running init\_db twice doesn't break anything|
|first\_time\_set\_does\_not\_require\_current|First password set needs no current password|
|change\_requires\_current|Password change requires current password|
|change\_with\_wrong\_current\_rejected|Wrong current password blocks change|
|change\_with\_correct\_current\_succeeds|Correct current password allows change|
|old\_password\_no\_longer\_works\_after\_change|Old password is invalidated after change|
|index\_exists|Unique display\_name index exists on users table|
|create\_invite\_rejects\_duplicate\_user\_name|Can't create invite for already-taken display name|
|create\_invite\_rejects\_case\_insensitive\_duplicate|Duplicate names rejected case-insensitively|
|create\_invite\_rejects\_duplicate\_pending\_invite|Can't create two pending invites for same name|
|db\_index\_rejects\_direct\_duplicate\_insert|DB-level unique constraint catches direct duplicates|
|patch\_user\_no\_fields|PATCH with no fields is a no-op|
|patch\_nonexistent\_user|PATCHing a nonexistent user returns 404|
|promote\_to\_admin|Admin can promote another user to admin|
|deactivate\_already\_deactivated|Deactivating an already-inactive user is safe|
|portal\_redirects\_when\_unauthenticated|Portal page redirects to login if no session|
|portal\_serves\_html\_when\_authenticated|Portal page loads for authenticated users|
|admin\_redirects\_when\_unauthenticated|Admin page redirects to login if no session|
|admin\_redirects\_non\_admin\_to\_portal|Non-admin gets bounced to portal|
|admin\_serves\_html\_for\_admin\_user|Admin page loads for admin users|
|login\_page\_always\_served|Login page never requires auth|
|portal\_redirects\_with\_invalid\_cookie|Garbage session cookie redirects to login|
|csrf\_allows\_same\_origin|Same-origin requests pass CSRF check|
|csrf\_allows\_missing\_origin|Requests with no origin header pass|
|csrf\_factory\_rejects\_bad\_origin|Mismatched origin is rejected|
|csrf\_factory\_allows\_matching\_origin|Correct origin passes|
|csrf\_factory\_skips\_in\_dev\_mode|CSRF check disabled in dev mode|
|kill\_session\_nonexistent\_prefix|Deleting nonexistent session by prefix is safe|
|kill\_session\_empty\_prefix|Empty prefix returns failure|
|create\_app\_uppercase\_id|App ID must be lowercase|
|create\_app\_duplicate\_id|Duplicate app ID is rejected|
|create\_app\_missing\_slash\_prefix|Route prefix must start with slash|
|patch\_app\_no\_fields|PATCH app with no fields is a no-op|
|patch\_nonexistent\_app|PATCHing nonexistent app returns 404|
|create\_app\_very\_long\_fields|Oversized app fields are rejected|
|empty\_session\_cookie|Empty cookie value redirects to login|
|garbage\_session\_cookie|Random garbage cookie redirects to login|
|deleted\_session\_is\_invalid|Deleted session can't be reused|
|create\_user\_returns\_password|Admin-created user gets a generated password|
|created\_user\_can\_login\_with\_password|Admin-created user can log in immediately|
|create\_user\_admin\_flag|Admin flag is set correctly on user creation|
|create\_user\_duplicate\_name|Duplicate display name is rejected|
|create\_user\_requires\_apps|User creation requires at least one app|
|create\_user\_requires\_admin|Only admins can create users|
|reset\_password\_changes\_password|Admin can reset any user's password|
|reset\_password\_user\_not\_found|Reset for unknown user returns 404|
|health\_returns\_200\_when\_healthy|/health returns 200 when DB is up|
|health\_is\_unauthenticated|/health requires no auth|
|logout\_response\_contains\_delete\_cookie\_header|Logout response clears the cookie|
|logout\_deletes\_server\_side\_session|Logout removes session from DB|
|logout\_without\_cookie\_does\_not\_error|Logout with no cookie is safe|
|valid\_session\_2xx\_refreshes\_cookie|Successful request refreshes session cookie|
|invalid\_session\_redirect\_does\_not\_refresh\_cookie|Redirect response doesn't touch cookie|
|401\_response\_does\_not\_refresh\_cookie|401 response doesn't touch cookie|
|expired\_session\_is\_rejected|Sessions past expiry are refused|
|valid\_session\_extends\_expiry\_on\_validate|Valid session sliding window works|
|new\_session\_has\_expires\_at\_set|New sessions always have an expiry|
|user\_password\_change\_kills\_other\_sessions|Changing password revokes all other sessions|
|user\_password\_change\_keeps\_current\_session|Current session survives password change|
|admin\_password\_reset\_kills\_all\_sessions|Admin reset revokes all user sessions|
|admin\_reset\_logs\_session\_count|Session kill count is logged|

\---

## test\_checkbox\_mapping.py — SWPPP PDF checkbox logic (4 tests)

|Test|Description|
|-|-|
|extract\_checkbox\_rows\_matches\_config\_count|Extracted rows match YAML config count|
|generate\_batch\_fills\_checkbox\_values|Batch generation fills checkbox fields correctly|
|populate\_checkbox\_targets\_assigns\_inferred\_fields|Inferred checkbox targets get assigned|
|build\_audit\_mapping\_document\_includes\_checkbox\_targets|Audit export includes checkbox mappings|

\---

## test\_fill.py — PDF field filling (3 tests)

|Test|Description|
|-|-|
|build\_field\_updates\_uses\_explicit\_targets|Explicit field targets override inferred ones|
|generate\_batch\_requires\_fillable\_fields|Batch generation fails without required fields|
|generate\_batch\_returns\_empty\_when\_no\_dates|No dates = no PDFs generated|

\---

## test\_mesonet.py — Rain data parsing and fetching (14 tests)

|Test|Description|
|-|-|
|parses\_normal\_rows|Normal CSV rows parse correctly|
|skips\_missing\_data|Rows with missing data are skipped|
|skips\_empty\_rainfall|Empty rainfall field is skipped|
|empty\_input|Empty input returns empty result|
|header\_only|Header-only CSV returns empty result|
|filters\_above\_threshold|Only days above threshold are returned|
|exact\_threshold\_included|Day exactly at threshold is included|
|just\_above\_threshold|Day just above threshold is included|
|custom\_threshold|Custom threshold value is respected|
|all\_missing\_produces\_empty|All-missing data returns empty|
|counts\_failed\_days|Failed day count is tracked correctly|
|counts\_missing\_days|Missing day count is tracked correctly|
|partial\_failure\_emits\_warning|Partial Mesonet failure logs a warning|
|partial\_failure\_with\_missing\_emits\_warning|Combined partial failure logs warning|

\---

## test\_model.py — Data model normalization (3 tests)

|Test|Description|
|-|-|
|template\_map\_normalizes\_legacy\_shapes|Old YAML shapes normalize to current model|
|template\_map\_preserves\_explicit\_pdf\_targets|Explicit PDF targets are not overwritten|
|checkbox\_item\_defaults\_are\_safe|Checkbox item defaults don't cause errors|

\---

## test\_onboarding.py — Multi-tenant company signup and employee invites (34 tests)

|Test|Description|
|-|-|
|company\_signup\_invites\_table\_exists|Signup invites table is in the schema|
|invite\_codes\_has\_company\_id\_and\_role|Invite codes table has tenant columns|
|users\_has\_email\_column|Users table has email column|
|create\_and\_get|Signup invite can be created and retrieved|
|get\_all\_company\_signup\_invites|All signup invites can be listed|
|claim\_creates\_company\_and\_user|Claiming signup invite creates company + admin user|
|claim\_marks\_invite\_claimed|Claimed invite is marked as used|
|double\_claim\_returns\_none|Claiming an already-claimed invite fails|
|expired\_invite\_returns\_none|Expired invite cannot be claimed|
|get\_missing\_invite\_returns\_none|Missing token returns None|
|create\_employee\_invite\_returns\_code|Employee invite generates a code|
|claim\_employee\_invite\_adds\_to\_company|Claiming employee invite adds user to company|
|invalid\_role\_on\_employee\_invite\_raises|Invalid role raises an error|
|all\_three\_roles\_produce\_correct\_membership|All valid roles create correct membership|
|platform\_admin\_can\_create\_signup\_invite|Platform admin can send company signup invite|
|non\_platform\_admin\_gets\_403|Non-platform-admin can't create signup invites|
|unauthenticated\_gets\_401|Unauthenticated request gets 401|
|list\_signup\_invites|Platform admin can list all signup invites|
|get\_invite\_info\_valid|Valid token returns invite info|
|get\_invite\_info\_missing\_returns\_404|Missing token returns 404|
|full\_signup\_flow|End-to-end: invite → claim → logged in|
|signup\_with\_bad\_token\_returns\_400|Bad token returns 400|
|company\_admin\_can\_list\_members|Company admin can list their company's members|
|non\_member\_gets\_403|Non-member can't list company members|
|platform\_admin\_can\_list\_any\_companys\_members|Platform admin can see any company's members|
|company\_admin\_can\_create\_employee\_invite|Company admin can invite employees|
|pm\_cannot\_create\_employee\_invite|PM role can't invite employees|
|viewer\_cannot\_create\_employee\_invite|Viewer role can't invite employees|
|invalid\_role\_in\_employee\_invite\_returns\_400|Invalid role in invite returns 400|
|update\_member\_role|Company admin can change a member's role|
|remove\_member|Company admin can remove a member|
|cannot\_remove\_self|Company admin can't remove themselves|
|platform\_admin\_sees\_all\_companies|Platform admin can list all companies|
|non\_platform\_admin\_gets\_403 (companies)|Regular user can't list all companies|

\---

## test\_rain\_fill.py — Rain event PDF generation (7 tests)

|Test|Description|
|-|-|
|creates\_correct\_number\_of\_pdfs|One PDF per rain event date|
|filenames\_follow\_pattern|Output filenames match expected format|
|inspection\_type\_prepended|Inspection type appears in filename|
|empty\_original\_type|Missing inspection type handled gracefully|
|correct\_date\_in\_pdf|Report date matches the rain event date|
|empty\_rain\_days\_returns\_empty|No rain days = no PDFs|
|zip\_bundle\_created|ZIP is created when multiple PDFs generated|

\---

## test\_session.py — Desktop app session persistence (13 tests)

|Test|Description|
|-|-|
|save\_creates\_file|Saving a session writes a file|
|load\_returns\_saved\_data|Loaded session matches what was saved|
|load\_missing\_file\_returns\_none|Missing session file returns None|
|load\_corrupt\_json\_returns\_none|Corrupt JSON session returns None|
|load\_future\_version\_returns\_none|Session from newer schema version is rejected|
|load\_missing\_version\_returns\_none|Session with no version field is rejected|
|save\_atomic\_write|Session save is atomic (no partial writes)|
|save\_creates\_directory|Save creates the directory if it doesn't exist|
|named\_round\_trip|Named session saves and loads correctly|
|list\_sessions\_sorted|Session list is sorted by name|
|list\_sessions\_empty|Empty session directory returns empty list|
|delete\_session\_removes\_file|Deleting a session removes its file|
|delete\_nonexistent\_session\_no\_error|Deleting a missing session doesn't crash|

\---

## test\_swppp\_api.py — SWPPP web API endpoints (76 tests)

|Test|Description|
|-|-|
|unauthenticated\_returns\_401|API requires auth|
|no\_swppp\_access\_returns\_403|User without SWPPP app access gets 403|
|returns\_fields\_and\_groups|Form schema endpoint returns fields and groups|
|checkbox\_group\_structure|Checkbox groups have correct structure|
|total\_questions|Schema has expected number of questions|
|returns\_station\_list|Stations endpoint returns list|
|station\_structure|Station records have correct fields|
|list\_empty|Empty session list returns empty array|
|save\_and\_get|Session can be saved and retrieved|
|list\_after\_save|Saved session appears in list|
|delete|Session can be deleted|
|get\_nonexistent\_returns\_404|Getting missing session returns 404|
|export\_session|Session exports to JSON|
|import\_session\_no\_save|Import without save flag doesn't persist|
|import\_session\_with\_save|Import with save flag persists|
|generate\_zip|Generate produces a ZIP file|
|generate\_no\_dates\_returns\_400|Generate without dates returns 400|
|generate\_with\_checkboxes|Checkbox values appear in generated PDF|
|invalid\_file\_returns\_400|Invalid template file returns 400|
|parse\_csv\_empty\_file|Empty CSV returns empty rain data|
|parse\_csv\_oversized\_file|Oversized CSV is rejected|
|parse\_csv\_binary\_content|Binary content CSV is rejected|
|parse\_csv\_negative\_threshold|Negative threshold is rejected|
|rain\_fetch\_invalid\_station|Unknown station returns error|
|rain\_fetch\_invalid\_date\_format|Bad date format returns error|
|rain\_fetch\_end\_before\_start|End date before start is rejected|
|rain\_fetch\_negative\_threshold|Negative threshold rejected|
|rain\_fetch\_threshold\_too\_high|Threshold over maximum rejected|
|rain\_fetch\_valid|Valid rain fetch returns data|
|rain\_fetch\_network\_error|Network failure returns 502|
|rain\_fetch\_returns\_events|Fetch returns correct rain events|
|generate\_missing\_start\_date|Missing start date rejected|
|generate\_missing\_end\_date|Missing end date rejected|
|generate\_malformed\_rain\_day\_date|Bad rain day date rejected|
|generate\_empty\_rain\_days\_list|Empty rain days list accepted|
|generate\_with\_rain\_days|Rain days appear in generated report|
|generate\_with\_all\_checkbox\_groups|All checkbox groups render correctly|
|generate\_with\_notes|Notes field appears in report|
|generate\_unknown\_checkbox\_group\_ignored|Unknown checkbox group doesn't crash|
|generate\_very\_long\_field\_values|Long field values are rejected|
|generate\_negative\_rain\_amount\_rejected|Negative rain amount rejected|
|reject\_illegal\_characters|Illegal characters in fields rejected|
|reject\_slash\_in\_name|Slash in session name rejected|
|accept\_typical\_names|Normal session names accepted|
|save\_empty\_body|Empty save body rejected|
|save\_very\_long\_name|Oversized name rejected|
|get\_very\_long\_name|Oversized name in GET rejected|
|save\_special\_chars\_in\_name|Special chars in name rejected|
|delete\_nonexistent\_session|Deleting missing session is safe|
|export\_nonexistent\_returns\_404|Exporting missing session returns 404|
|import\_missing\_session\_name|Import without name rejected|
|import\_oversized\_file|Import file over size limit rejected|
|import\_non\_json\_content|Non-JSON import rejected|
|import\_json\_array\_not\_dict|JSON array instead of object rejected|
|import\_save\_and\_verify|Import with save verified end-to-end|
|invalid\_start\_date\_rejected|Non-ISO start date rejected|
|end\_before\_start\_rejected|End before start rejected|
|range\_exceeding\_365\_days\_rejected|Generate range over 365 days rejected|
|exactly\_365\_days\_accepted|Exactly 365 days accepted|
|rain\_days\_over\_limit\_rejected|Too many rain days in request rejected|
|project\_field\_value\_too\_long\_rejected|Field value over max length rejected|
|invalid\_date\_format\_rejected|Bad date format in rain fetch rejected|
|range\_over\_730\_days\_rejected|Rain fetch over 730 days rejected|
|mesonet\_completely\_unavailable\_returns\_502|Full Mesonet outage returns 502|
|mesonet\_timeout\_returns\_502|Mesonet timeout returns 502|
|partial\_failure\_still\_returns\_200\_with\_counts|Partial failure still succeeds with counts|
|generate\_batch\_empty\_returns\_500|Empty batch generation returns 500|
|generate\_batch\_raises\_returns\_500|Generation crash returns 500|
|zip\_bundle\_failure\_returns\_500|ZIP failure returns 500|
|swppp\_index\_serves\_html|SWPPP index page serves HTML|
|swppp\_index\_contains\_alpine|SWPPP page loads Alpine.js|
|health\_returns\_200\_when\_healthy|Health endpoint returns 200|
|health\_is\_unauthenticated|Health endpoint needs no auth|
|health\_fails\_when\_template\_missing|Health fails if PDF template missing|
|health\_fails\_when\_mapping\_missing|Health fails if YAML mapping missing|
|lifespan\_raises\_if\_template\_missing|App won't start without template|
|lifespan\_raises\_if\_mapping\_missing|App won't start without mapping|
|session\_list\_db\_error\_logged|DB error on session list is logged|
|session\_save\_db\_error\_logged|DB error on session save is logged|

\---

## test\_template\_integration.py — Real PDF template (1 test)

|Test|Description|
|-|-|
|real\_template\_text\_fields\_fill|Text fields fill correctly against real PDF template|

\---

## test\_tenant\_isolation.py — Multi-tenant data isolation (23 tests)

|Test|Description|
|-|-|
|companies\_table\_exists|Companies table is in the schema|
|users\_has\_is\_platform\_admin\_column|Platform admin flag exists on users|
|companies\_schema\_columns|Companies table has all required columns|
|company\_users\_schema\_columns|Company\_users table has all required columns|
|create\_and\_get\_company|Company can be created and retrieved|
|slug\_generated\_lowercase\_no\_spaces|Company slug is lowercase, URL-safe|
|slug\_unique\_on\_name\_collision|Duplicate company names get unique slugs|
|get\_company\_by\_slug|Company lookup by slug works|
|get\_all\_companies\_returns\_both|Platform admin sees all companies|
|get\_company\_returns\_none\_for\_missing|Missing ID returns None|
|get\_company\_by\_slug\_returns\_none\_for\_missing|Missing slug returns None|
|add\_and\_get\_company\_user|User can be added to a company|
|all\_three\_valid\_roles\_accepted|All valid roles are accepted|
|invalid\_role\_raises|Invalid role raises ValueError|
|get\_company\_members\_returns\_only\_that\_company|Members query is scoped to one company|
|get\_user\_companies\_returns\_memberships|User's company list returns correctly|
|user\_in\_company\_a\_has\_no\_membership\_in\_company\_b|Cross-tenant membership doesn't exist|
|company\_members\_excludes\_inactive\_records|Inactive members are excluded|
|two\_companies\_same\_slug\_prefix\_get\_distinct\_slugs|Similar names produce distinct slugs|
|admin\_user\_gets\_is\_platform\_admin|Admin user gets platform admin flag|
|regular\_user\_has\_no\_platform\_admin|Regular user has no platform admin flag|
|validate\_session\_includes\_is\_platform\_admin|Session validation returns platform admin flag|
|validate\_session\_regular\_user\_platform\_admin\_false|Regular user session has false platform admin|

\---

## Summary

|File|Tests|
|-|-|
|test\_auth.py|104|
|test\_checkbox\_mapping.py|4|
|test\_fill.py|3|
|test\_mesonet.py|14|
|test\_model.py|3|
|test\_onboarding.py|34|
|test\_rain\_fill.py|7|
|test\_session.py|13|
|test\_swppp\_api.py|76|
|test\_template\_integration.py|1|
|test\_tenant\_isolation.py|23|
|**TOTAL**|**289**|

Next baseline: **319+** after IR-1 (Projects table + API + project creation flow).

