# Fedora Release Bot

This is a very simplistic bot that periodically runs and checks for new releases of osbuild or osbuild-composer.

If it finds a new release it:

 * schedules builds in Koji
 * updates Bodhi