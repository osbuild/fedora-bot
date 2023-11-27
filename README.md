# Fedora Release Bot

This is a very simplistic bot (`fedora_bot.py`) that periodically runs and checks for new releases of osbuild or osbuild-composer.

It looks for open pull requests created by Packit. It then checks whether CI has passed and if so, merges the pull request.

 # Reminder Bot

The reminder bot (`reminder_bot.py`) sends notifications to the team's Slack channel about whose turn it is to be a foreperson.