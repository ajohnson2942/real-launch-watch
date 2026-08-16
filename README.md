Watch-A-Launch

Get push notifications on your phone and desktop before SpaceX rocket launches happen, completely automatic and free!

Watch-A-Launch checks upcoming launch data every hour and keeps the schedule, countdowns, calendar, and notifications updated automatically. It will update if any rocket launch times change. 

What Watch-A-Launch does
Sends push notifications before launches
Gives a 24-hour reminder
Gives a 3-hour reminder
Includes the exact scheduled launch time
Sends a new notification if a launch is delayed or its scheduled time changes
Lets you choose notifications for:
California launches only
Florida launches only
Both California and Florida
Shows the next launch with a live countdown
Shows upcoming launches for the current month
Shows a calendar with predicted launch dates, for one month ahead. 
Updates automatically as new launches are added or existing launches move
Notification locations

Watch-A-Launch uses separate ntfy topics for each state:

CoolRockets-CA — California launch notifications
CoolRockets-FL — Florida launch notifications

If you want alerts for both states, subscribe to both topics.

You can also use the notification selector on the Watch-A-Launch homepage to see which topic or topics correspond to your choice.

Calendar

The launch calendar automatically displays the current month and year.

It changes automatically when a new month begins, so no manual updates are required.

The calendar uses:

Yellow X = at least one tracked launch is currently scheduled or predicted for that date in California
Light Blue X = at least one tracked launch is currently scheduled or predicted for that date in Florida

If both California and Florida have launches scheduled for the same day, both colored X's will appear on that date.

Launch schedules can change, so the X]s automatically move when updated launch data is received.

Upcoming launches

The Upcoming section only shows launches scheduled for the current month.

For example:

In August, it shows the remaining August launches
When September begins, it automatically switches to September launches
When October begins, it automatically switches to October launches

Once a launch's scheduled time has passed, it automatically disappears from the Upcoming list.

The calendar can still keep the X on that date for the remainder of the month.

Automatic updates

The GitHub Action checks for new launch information once every hour.

Each successful check updates the launch data used by the app.

This means Watch-A-Launch automatically keeps track of:

Newly announced launches
Launch delays
Launch date changes
Launch time changes
Updated launch locations
Upcoming launch dates for the calendar

The app does not require a manually maintained list of launches.

Schedule changes

Rocket launch schedules frequently change.

If a launch time changes, Watch-A-Launch detects the updated schedule during one of its hourly checks.

For example, if a launch was originally scheduled for:

3:00 PM

and later moves to:

4:00 PM

the appropriate California or Florida notification topic receives an updated launch-time notification.

Future 24-hour and 3-hour reminders are then calculated using the new launch time.

Launch data

Watch-A-Launch uses structured launch information for its primary launch schedule and can use Spaceflight Now as a fallback source.

This allows the app to retrieve future launch dates for the calendar without depending entirely on the visual layout of one website.

The generated launch data is stored in:

docs/launches.json

The dashboard reads that file to display the next launch, calendar, and current month's upcoming launches.

Automatic browser updates

When Watch-A-Launch is opened, it loads the latest generated launch schedule.

If the app is left open continuously, it periodically reloads the launch data automatically.

This does not create additional GitHub Actions runs. It only reloads the already-generated schedule file.

The current month and Upcoming list are also checked locally, so they can roll over automatically when the date changes.

Free services used
Piece	What it does	Cost
GitHub	Hosts the project	Free
GitHub Actions	Checks launch information once per hour	Free
ntfy.sh	Delivers launch notifications	Free
GitHub Pages	Hosts the Watch-A-Launch dashboard	Free
Files in this project
launch_source.py — retrieves and normalizes upcoming launch information
scraper.py — Spaceflight Now schedule parser/fallback
notifier.py — updates launch data and sends notifications
config.json — launch filtering and notification settings
data/state.json — remembers previous schedules and notifications that have already been sent
docs/index.html — Watch-A-Launch dashboard
docs/launches.json — automatically generated launch data used by the dashboard
.github/workflows/check-launches.yml — runs the automatic launch checker once per hour
Version

Current version:

v1.0.1

Changelog:

Changed the color scheme and fonts of the app, as well as changed the name from Launch-Watch to Watch-A-Launch. Added the ability to select what state's launches you want to get notified for, as well as added a calendar that shows the dates and times of the next launches.
