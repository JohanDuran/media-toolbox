# short.json — the Shorts spec

A vertical Short (1080×1920, 30 fps) described as data. Put `short.json` in a project folder next to
the narration transcript and the screen recordings; the toolbox builds everything from it:

| Tool (MCP / dashboard) | What it does |
|---|---|
| `short_info` | Reads the spec: length, every shot with its start/end/record frame, which outputs exist. Instant. |
| `short_build` | Builds the shots (V1), the graphics + captions overlay (V2), the narration (A1) and the music/SFX bed (A2). `only` rebuilds single shots. |
| `short_preview` | Composites everything with sound into `edit/preview.mp4` + a contact sheet. |
| `short_frames` | A labelled grid of frames from a recording, for finding crops and moments. |

The dashboard's **Shorts** page lists every folder with a `short.json` under `SHORTS_ROOT`.

## The idea: everything is anchored to words

The narration is transcribed with word timings (`transcribe` → `transcripts/narration.words.tsv`).
Times in the spec can point at words, so a new voice take re-times the whole edit: cuts, zooms,
labels and sound effects all follow.

### Time expressions

A number, or a string of terms joined by ` + ` / ` - ` (spaces around the operator):

| Term | Meaning |
|---|---|
| `1.5` | seconds |
| `word:so` | start of the first "so" in the narration (case and punctuation ignored) |
| `word:use#2` | start of the second "use" |
| `word:so.end` | end of the first "so" |
| `chat` | a named anchor from `anchors` (anchors are resolved top to bottom and can use earlier ones) |
| `words_end` | end of the last spoken word |
| `start`, `dur` | only inside a shot's zoom `keys`: the shot's start time and its length |

Examples: `"word:this#2 - 0.06"`, `"folder_word - start + 0.3"`, `"words_end + 0.9"`.

## Format

```jsonc
{
  "version": 1,
  "title": "Claude Chat vs Claude Code",
  "words": "transcripts/narration.words.tsv",        // default

  "sources": { "chat": "capture/chat_tab.mkv", "still": "capture/frame.png" },  // name -> file
  "regions": { "app": [0, 0, 1920, 1032] },           // name -> [x, y, w, h] on the recording
  "blurs":   { "pc_name": [[1208, 192, 196, 24]] },    // name -> rectangles to blur (privacy)
  "anchors": { "chat": "word:this#1 - 0.06", "end": "words_end + 0.9" },

  "shots": [                                          // back to back: each starts where the last ended
    { "name": "s01_hook", "end": "chat", "note": "free text, shown in the dashboard",
      "layers": [                                     // 0 or more crops, drawn in order
        { "src": "chat",                              // a source name or a path
          "ss": 50.1,                                 // where to start in the source (seconds)
          "speed": 1,                                 // >1 = faster; or "speed_to": [from, to] to fit a span
          "still": false,                             // true for images
          "region": "app",                            // name or [x, y, w, h]
          "blur": ["pc_name"],                        // names or rectangles
          "card_w": 1080, "cy": 900,                  // card width and its centre y in the 1080x1920 frame
          "dim": false,                               // blur + darken (behind end cards)
          "keys": [[0, 2.2, 260, 70], ["dur", 1.0, 960, 516]]  // [time, zoom, focus x, focus y], eased
        }
      ]
    }
  ],

  "graphics": [
    { "start": 0, "end": "chat", "style": "Head", "pos": [540, 300], "text": "SAME BRAIN" },
    { "start": "chat", "end": "code", "style": "Head", "pos": [540, 230],
      "runs": [["CHAT ", "blue"], ["= THINKING PARTNER", "white"]] },     // several colours
    { "start": "ask", "end": "code", "style": "Tag", "pos": [200, 385], "fs": 42, "box": "blue", "text": "ASK" },
    { "start": "rule", "end": "cta", "pos": [90, 520],                     // a rounded card; pos = top-left
      "shape": { "w": 900, "h": 330, "r": 36, "fill": "card", "border": "blue", "bord": 4 } }
  ],
  "captions": { "y": 1480, "max_words": 3 },          // word-by-word, current word yellow ("enabled": false to turn off)

  "audio": {
    "narration": "audio/narration.wav",               // leveled to about -14 LUFS
    "music": "../sfx_library/some-music.wav", "music_db": -24,   // loops if shorter than the video
    "sfx": [ { "file": "../sfx_library/whoosh.wav", "peak": 0.93, "at": ["chat", "code"], "gain": -12 } ]
  },                                                   // peak = where the sound is loudest, so it lands ON the time

  "backdrop": { "c0": "0x16203a", "c1": "0x06080d" }, // radial gradient behind the cards, or { "image": "edit/bg.png" }
  "outputs": { "shots": "edit/shots" }                 // optional; defaults below
}
```

### Graphics fields

| Field | Meaning |
|---|---|
| `style` | `Head` (bold title), `Big` (huge number/title), `Tag` (label in a coloured box), `Card` (plain text, top-left aligned) |
| `pos` | `[x, y]` centre (top-left for `Card` and `shape`) |
| `text` / `runs` | text (`\N` = new line) / `[[text, colour], ...]` |
| `fs`, `bold`, `color`, `box`, `strike` | size, bold on/off, text colour, Tag box colour, strike-through |
| `pop` | pop-in animation (default `true`); `fade`: `[in_ms, out_ms]` |
| `layer` | draw order (captions are on 5) |

Colours: `white yellow green red orange grey blue darkgreen ink card black`, or `#RRGGBB`.

### Default outputs (inside the project folder)

`edit/shots/<name>.mp4` (+ `thumbs/`), `edit/overlay.mov` + `overlay.ass`, `edit/bg.png`,
`audio/narration_final.wav`, `audio/bed.wav`, `edit/preview.mp4` + `preview_sheet.jpg`.

## Resolve

`short_build` returns a `timeline` list: every file with its track (V1 shots, V2 overlay, A1 voice,
A2 bed) and `record_frame` from the timeline start — exactly what Resolve's `append_to_timeline` needs.

## Workflow for a new Short

1. Script → `speak` per line → join → `transcribe` the narration (word timings).
2. Record the screen (ffmpeg gdigrab or OBS); use `short_frames` to find crops and moments.
3. Write `short.json` (copy the last project's and change sources, shots, graphics).
4. `short_build` → `short_preview` → check the contact sheet → fix single shots with `only`.
5. Import into Resolve with the returned `timeline`, render.
