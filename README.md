# Fedora Release Bot (Composite Action)

This is a very simplistic bot (`fedora_bot.py`) that can easily be triggered from any repository e.g. `on push: tags`.

If it finds a new release it:

 * merges open pull requests created by Packit (if all tests passed)
 * updates Bodhi (to be replaced by Packit)


Here is an example on how to include this in your repository's github workflows:
```
# Example for fedora-bot's GitHub Composite Action
name: "fedora-bot"

on:
  push:
    tags:
      - "v*"

jobs:
  check:
    name: Check for new releases
    runs-on: ubuntu-latest

    container:
      image: ghcr.io/osbuild/fedora-bot:latest

    steps:
      - name: Upstream release
        uses: osbuild/fedora-bot@composite-action
        with:
          fedora_username: "{{ secrets.FEDORA_USERNAME }}"
          fedora_password: "{{ secrets.FEDORA_PASSWORD }}"
          fedora_apikey: "{{ secrets.FEDORA_APIKEY }}"
          num_tests: 2
          slack_webhook_url: "${{ secrets.SLACK_WEBHOOK_URL }}"
```
