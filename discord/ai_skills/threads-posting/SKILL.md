---
name: threads-posting
description: Draft, edit or publish text posts and replies on Meta Threads through the configured signed-in browser profile; use only for Meta Threads, not Discord discussion threads.
---

Load the browser skill for browser tools and, when useful, its Node REPL reference.
Use the managed browser page with the profile's existing login. Never request,
read or export passwords, cookies, access tokens or browser storage. If sign-in is
required, stop the account-dependent work and explain that the profile needs to
be signed in. Do not fill login or verification fields.
Threads account interactions are available only to bot owners or publishers
explicitly enabled by an owner with `ai-config threads-publisher`.

Determine whether the user wants a draft, a new post, or a reply to a particular
post. For replies, inspect the target and context first. Preserve supplied wording,
links and the intended audience. If asked to compose text, produce a complete
draft. Do not invent personal experiences, endorsements or facts about the user.

Publish only when the user explicitly requests publication and the intended text
or delegated writing brief and target account are clear. Requests to add, inspect
or test this skill authorize no publication. A request for a draft ends with the
draft; do not press Post. If an essential detail is missing, ask for that detail.
An already explicit publish instruction does not need a second confirmation.

Navigate to the public Threads website and inspect its current UI. Verify the
visible signed-in account before composing; if the requested account differs,
stop rather than switching profiles or posting as another account. Identify the
composer, editable field and publishing control from fresh snapshots. Follow the
UI's current length limits; do not silently split a post into multiple posts.
Use the ordinary browser approval flow and honor a rejected session.

For an authorized publication, enter the intended text, inspect the resulting
draft, then activate the publishing control once. Verify the resulting post or
reply and return its direct permalink. If the action times out or the result is
ambiguous, inspect the profile/target conversation before deciding whether it
succeeded. Never retry a publish blindly or report success without evidence.

This skill supports text and links. The managed browser does not support media
uploads. Do not fake attachments or substitute a screenshot for an uploaded image.
Do not like, follow, repost, delete posts or change account settings unless those
actions were separately requested.
