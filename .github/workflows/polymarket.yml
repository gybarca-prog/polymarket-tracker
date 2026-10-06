name: Polymarket Whale Tracker

on:
  workflow_dispatch:
    inputs:
      min_usd:
        description: "Minimum trade size in USD"
        required: false
        default: "10000"

  schedule:
    - cron: "*/10 * * * *"

jobs:
  tracker:
    runs-on: ubuntu-latest

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Run tracker
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
          TELEGRAM_CHAT_ID: ${{ secrets.TELEGRAM_CHAT_ID }}
        run: |
          python3 whale_tracker.py poll \
            --min-usd "${{ github.event.inputs.min_usd || '10000' }}"
