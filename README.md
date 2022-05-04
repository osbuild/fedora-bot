# Fedora Release Bot

This is a very simplistic bot (`fedora_bot.py`) that periodically runs and checks for new releases of osbuild or osbuild-composer.

If it finds a new release it:

 * merges open pull requests created by Packit
 * schedules builds in Koji (to be replaced by Packit)
 * updates Bodhi (to be replaced by Packit)

 # Reminder Bot

 The reminder bot (`reminder_bot.py`) sends notifications to the team's Slack channel about whose turn it is to be a foreperson.