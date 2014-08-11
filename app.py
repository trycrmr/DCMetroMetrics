#!/usr/bin/env python
"""
This is the main program. Running this module will cause
the twitter accounts to live tweet.

For testing purposes, run the test_app.py program, which
disables live tweeting.
"""

import imp
import os
import sys
import subprocess
import argparse
import copy


PY_DIR = os.environ['PYTHON_DIR']
REPO_DIR = os.environ['REPO_DIR']
SCRIPT_DIR = os.path.join(REPO_DIR, 'scripts')
DATA_DIR = os.environ['DATA_DIR']

# Import application modules
from ELESAppRunner import App as ELESApp
# from elevatorApp import ElevatorApp
from hotCarApp import HotCarApp

# Import the web server and web page generator
import gevent
from gevent import monkey; monkey.patch_all() # Needed for bottle

def run(LIVE=False):

   # Do not run as live unless this module is main
   if __name__ != "__main__":
       LIVE = False

   # Run the web server
   # Update: Do not run let the bottle app act as the webserver.
   # Instead, gunicorn is used to run bottle as a WSGI app.
   #serverApp = Server()
   #serverApp.start()

   # Run MetroEscalators/MetroElevators twitter App
   elesApp = ELESApp(LIVE=LIVE)
   elesApp.start()

   # Run HotCar twitter app
   hotCarApplication = HotCarApp(LIVE=LIVE)
   hotCarApplication.start()

   # Run the web page generator
   # webPageGenerator = WebPageGenerator()
   # webPageGenerator.start()

   # Run forever
   while True:
       gevent.sleep(10)

if __name__ == '__main__':
   #run(LIVE=True)
   run(LIVE=False)
