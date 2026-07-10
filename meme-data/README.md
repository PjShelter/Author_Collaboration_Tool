# meme-generator Data

This directory is mounted into the `meme-generator` container as `/data`.

Install or update the `meme_emoji` templates with:

```bash
bash scripts/setup_meme_emoji.sh
```

The Docker Compose service loads extra templates from:

```text
/data/memes/meme_emoji/emoji
```
