#!/usr/bin/python3
import os
import sys
import argparse
import yaml
from datetime import date, timedelta
from slack_sdk.webhook import WebhookClient


def all_wednesdays(year: int):
    d = date(year, 1, 1)
    d += timedelta(days = 2 - d.weekday())
    if d.year != year:
        d += timedelta(days = 2 - d.weekday() + 7)
    
    while d.year == year:
        yield d
        d += timedelta(days = 7)


def create_yearly_plan(components, year: int):
    for component in components:
        with open(f'{year}-{component}.yaml','w') as file:
            i = components.index(component)
            lines = ""
            for d in all_wednesdays(year):
                if i % 2:
                    lines += f"{d}: \n"
                i += 1
            file.write(lines)


def release_schedule(component: str):
    d = date.today()
    filename = f'{d.year}-{component}.yaml'
    if os.path.isfile(filename):
        with open(filename,'r') as file:
            release_dates = yaml.load(file, Loader=yaml.FullLoader)
    else:
        print(f"Error. The {filename} was not found. Please create it first.")
        sys.exit(1)

    return release_dates


def slack_notify(message: str):
    url = os.getenv('SLACK_WEBHOOK_URL')
    github_url = os.getenv('GITHUB_ACTION')

    print(f"msg: {message}\ngithub: {github_url}")

    webhook = WebhookClient(url)

    response = webhook.send(text=f'<{github_url}|reminder-bot>: :loudspeaker: {message}')
    assert response.status_code == 200
    assert response.body == "ok"


def send_reminder(components, target_date, message: str):
    for component in components:
        releases = release_schedule(component)
        for release_date, foreperson in releases.items():
            if release_date == target_date:
                slack_notify(f'{message} {component} release {release_date} by {foreperson}')


if __name__ == "__main__":
    """Main function"""
    components = ['osbuild-composer','osbuild']
    url = os.getenv('SLACK_WEBHOOK_URL')

    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("-y", "--year", help="Year to use to create yaml files for osbuild and osbuild-composer")
    parser.add_argument("-m", "--monthly", help="Send the monthly overview to our #osbuild-release Slack channel",
                        default=False, action=argparse.BooleanOptionalAction)
    parser.add_argument("-r", "--reminder", help="Send the release reminders on the scheduled date to our #osbuild-release Slack channel",
                        default=False, action=argparse.BooleanOptionalAction)
    args = parser.parse_args()

    if args.year is not None:
        create_yearly_plan(components, int(args.year))

    if args.reminder is True:
        message = "This week we have scheduled an"
        send_reminder(components, date.today() + timedelta(days=2), message) # send reminder on Monday before the release
        message = "Today we have scheduled an"
        send_reminder(components, date.today(), message)                     # send a reminder on the release day

    if args.monthly is True:
        message = "This month the following team members have signed up as forepeople :tada:"
        send_reminder(components, date.today(), message)                     # send an overview on the first of the month
