# Sending & building messages

[← docs home](./index.md)

All senders take a destination **mid** as the first argument. The mid prefix
decides the conversation type automatically:

| Prefix | Type | `toType` |
|--------|------|----------|
| `u…` | user | `USER` (0) |
| `c…` | group / chat | `GROUP` (2) |
| `r…` | room | `ROOM` (1) |
| `s…` | square chat | `SQUARE_CHAT` (4) |

## Quick senders

```python
to = "u0123456789abcdef0123456789abcdef"

api.send_text(to, "plain text")
api.reply_text(to, "a reply", related_message_id="14000000000000050")
api.send_sticker(to, package_id="11537", sticker_id="52002734")
api.send_location(to, 35.6586, 139.7454, title="Tokyo Tower", address="Minato")
api.send_contact(to, contact_mid="u....", display_name="Alice")
api.send_flex(to, "a flex message", {
    "type": "bubble",
    "body": {"type": "box", "layout": "vertical",
             "contents": [{"type": "text", "text": "Hello Flex!"}]},
})
```

Each returns the server's response (the persisted message, with its real `id`).

## Building a message yourself

`okline.Message` builds the `Message` struct; `send_message` posts it. Use this
when you need custom `contentMetadata` (mentions, custom stickers, …):

```python
from okline import Message

msg = Message.text(to, "hi @everyone", content_metadata={
    "MENTION": '{"MENTIONEES":[{"S":"3","E":"13","M":"u..."}]}',
})
api.send_message(msg)
```

`Message` factories: `text`, `sticker`, `location`, `contact`, `flex`,
`media_ref` (reference an already-uploaded OBS object). See
[`okline/models.py`](../okline/models.py).

## Reactions, unsend, read receipts

```python
from okline import enums

api.react("14000000000000050", enums.PredefinedReactionType.LOVE)   # NICE/LOVE/FUN/AMAZING/SAD/OMG
api.cancel_reaction("14000000000000050")

api.unsend_message("14000000000000050")            # recall a message you sent

api.send_chat_checked(chat_mid, last_message_id="14000000000000050")  # mark read
api.set_chat_hidden_status(chat_mid, last_message_id="…", hidden=True) # hide chat
api.send_chat_removed(chat_mid, last_message_id="…")                   # remove from list
```

## Reading message history

```python
api.get_recent_messages(message_box_id, count=50)
api.get_previous_messages(message_box_id, end_message_id="…",
                          delivered_time=1718900000000, count=100)
api.get_message_boxes(limit=100)                  # paginated chat list
api.get_message_boxes_by_ids([chat_mid])          # specific boxes
```

## `ContentType` reference

`Message.contentType` (from `okline.enums.ContentType`):

| Value | Name | | Value | Name |
|------:|------|-|------:|------|
| 0 | NONE *(text)* | | 13 | CONTACT |
| 1 | IMAGE | | 14 | FILE |
| 2 | VIDEO | | 15 | LOCATION |
| 3 | AUDIO | | 16 | POSTNOTIFICATION |
| 6 | CALL | | 17 | RICH |
| 7 | STICKER | | 18 | CHATEVENT |
| 9 | GIFT | | 19 | MUSIC |
| 12 | LINK | | 22 | FLEX |

Plain text uses `NONE` with the text in `Message.text`.

> **Media (images/video/files)** are uploaded to OBS first (`api.obs`), then sent
> as a message that references the object. See `api.determine_media_message_flow`
> and `okline/obs.py`.

## Want to see exactly what was sent?

Recording is on by default — paste the last request/response:

```python
api.send_text(to, "hi")
print(api.last.pretty())
```

→ [Recording](./recording.md)
