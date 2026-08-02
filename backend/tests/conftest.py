import os

# The app's lifespan starts an hourly Census refresh loop; tests must never
# reach the live API (phase-4 rule: recorded fixtures only in CI).
os.environ["CENSUS_AUTO_REFRESH"] = "0"
