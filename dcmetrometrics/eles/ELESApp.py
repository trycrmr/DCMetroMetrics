"""
eles.ELESApp

Base class for EscalatorApp and ElevatorApp, which 
drive MetroEscalators and MetroElevators
"""

# python modules
import os, sys
from time import sleep
from datetime import datetime, date, time, timedelta
from collections import defaultdict
import gc
# gc.set_debug(gc.DEBUG_STATS)

##########################################
# Set up logging
import logging
logger = logging.getLogger('ELESApp')
logger.setLevel(logging.DEBUG)
DEBUG = logger.debug
WARNING = logger.warning
INFO = logger.info

##########################################

# Custom modules
from ..common import dbGlobals, twitterUtils, utils, stations
from ..common.metroTimes import utcnow, tzutc, metroIsOpen, toLocalTime, isNaive
from ..common.globals import DATA_DIR, WWW_DIR
from ..common.JSONifier import JSONWriter
import dbUtils
from .dbUtils import invert_dict, update_db_from_incident
from .models import KeyStatuses, UnitStatus, SymptomCode, Unit, EscalatorAppState
from ..keys import WMATA_API_KEY
from twitter import TwitterError
from .Incident import Incident
from .WMATA_API import WMATA_API_ERROR, WMATA_API
from .defs import symptomToCategory, OPERATIONAL_CODE as OP_CODE

TWEET_LEN = 140

# Silence tweeting if the tick delay is greater than 20 minutes
SILENCE_GAP = 60*20

# Maximum number of tweets allowed in a single tick. Otherwise
# the tick is silenced
MAX_TICK_TWEETS = 10

PERFORMANCE_SUMMARY_INTERVAL = timedelta(hours = 4)

def url_maker(unit_id):
    url = "http://www.dcmetrometrics.com/unit/{unit_id}"
    return url.format(unit_id = unit_id)

def checkWMATAKey():
    """
    Check that the WMATA_API_KEY has been properly set.
    """
    if WMATA_API_KEY is None:
        msg = \
        """

        WMATA_API_KEY is required for the Elevator App and Escalator App.
        Check your keys.py

        For more information, see:
        https://github.com/LeeMendelowitz/DCMetroMetrics/wiki/API-Keys

        """
        from ..keys import MissingKeyError
        raise MissingKeyError(msg)

    if not isinstance(WMATA_API_KEY,str):
        raise TypeError('WMATA_API_KEY must be a str. Check your keys.py')

def daily_stats():
    """
    For each escalator, compute the statistics for the last 24 hours
    """

def getELESIncidents():
    """
    Get all escalator/elevator incidents from the WMATA API.
    This requires a valid WMATA API key
    """
    checkWMATAKey()
    api = WMATA_API(key=WMATA_API_KEY)
    res = api.getEscalator()
    incidents = res.json()['ElevatorIncidents']
    incidents = [Incident(i) for i in incidents]
    return incidents

#############################################
class ELESApp(object):
    """
    Base class for ElevatorApp and EscalatorApp
    """
    
    def __init__(self,
        LIVE=True,
        escTwitterKeys = None,
        eleTwitterKeys = None):

        self.LIVE = LIVE

        self.escTwitterKeys = escTwitterKeys
        self.eleTwitterKeys = eleTwitterKeys

        self.escTwitter = None
        self.eleTwitter = None

        if self.escTwitterKeys:
            self.escTwitter = twitterUtils.getApi(self.escTwitterKeys)

        if self.eleTwitterKeys:
            self.eleTwitter = twitterUtils.getApi(self.eleTwitterKeys)

        # Require that the WMATA Key is set.
        self.checkWMATAKey()

        self.dbg = dbGlobals.G()

        self.json_writer = JSONWriter(WWW_DIR)

    def getTwitterApi(self):

        if not self.LIVE:
            self.twitterApi = None
            return None

        if not self.twitterKeys.isSet():
            self.LIVE = False
            self.twitterApi = None
            return None

        if self.twitterApi is None:
            self.twitterApi = twitterUtils.getApi(self.twitterKeys)

        return self.twitterApi

    ######################################

    def checkWMATAKey(self):
        checkWMATAKey()

    def tick(self):

        curTime = utcnow()
        start_tick_time = curTime
        INFO("Start tick.")

        DEBUG("Running garbage collector at start of tick.")
        count = gc.collect()
        DEBUG("Garbage collect returned %i"%count)

        appState = EscalatorAppState.get()

        time_since_last_tick = None
        if appState.lastRunTime:
            time_since_last_tick = (curTime - appState.lastRunTime).total_seconds()

        # Get the current list of WMATA Incidents
        INFO("Getting ELES incidents from WMATA API.")
        incidents = getELESIncidents()

        INFO("Have %i outages."%len(incidents))

        # Update the database with units that changed status.
        INFO("Processing changed units.")
        changed_units = self.processIncidents(incidents, curTime)
        INFO("Have %i changed units"%len(changed_units))

        # Make tweets, but do not send them.
        INFO("Generating Tweets")
        tweets = self.generate_tweets(changed_units, url_maker = url_maker)

        # Update the unit's key statuses document.
        INFO("Updating key statuses.")
        for (unit_id, unit, old_status, new_status, key_status) in changed_units:
            # Update the Key Statuses Document.
            unit.update(new_status)

        # Update static json files.
        INFO("Updating static json files.")
        for (unit_id, unit, old_status, new_status, key_status) in changed_units:
            INFO("Writing json for unit: %s"%unit_id)
            self.json_writer.write_unit(unit)

        if changed_units:
        # if True:
            INFO("Writing station directory json.")
            self.json_writer.write_station_directory()

            INFO("Writing recent updates json.")
            self.json_writer.write_recent_updates()

        # Periodically recompute all unit performance summaries.
        # This takes about 2.5 minutes on my laptop, so we can
        # afford to do it during the application tick. It just means
        # some ticks will be long.
        if not appState.lastPerformanceSummaryTime or \
            (curTime - appState.lastPerformanceSummaryTime) > PERFORMANCE_SUMMARY_INTERVAL:

            INFO("Recomputing all performance summaries.")
            units = Unit.objects.no_cache()
            n = units.count()
            GARBAGE_COLLECT_DELTA = 20
            for i, unit in enumerate(units):

                INFO("Computing performance summary for unit %s: %i of %i (%.2f%%)"%(unit.unit_id, i, n, 100.0*i/n))

                # TODO: Could get the unit statuses here and pass to unit.compute_performance_summary
                # and to the json_writer. This way the statuses are pulled from the db only once.
                start_time = datetime.now()
                statuses = unit.get_statuses()
                unit.compute_performance_summary(statuses = statuses, save = True, end_time = start_tick_time )
                end_time=datetime.now()

                self.json_writer.write_unit(unit, statuses)

                if i%GARBAGE_COLLECT_DELTA == 0:
                    DEBUG("Running garbage collector in performance summary.")
                    count = gc.collect()
                    DEBUG("Garbage collect returned %i"%count)


            INFO("Writing station directory.")
            self.json_writer.write_station_directory()

            appState.lastPerformanceSummaryTime = curTime


        appState.lastRunTime = curTime
        appState.save()

        # Print tweets to screen.
        for t in tweets:
           INFO(t)

        ###############################################
        # Broadcast Tweets
        assert(len(tweets) == len(changed_units))
        units = [c[1] for c in changed_units]

        if self.LIVE and \
         len(tweets) <= MAX_TICK_TWEETS and \
         self.escTwitter is not None and \
         self.eleTwitter is not None:
            INFO("Broadcasting Tweets")
            self.broadcast_tweets(units, tweets)
        else:
            INFO("Not tweeting live.")

        DEBUG("Running garbage collector at end of tick.")
        count = gc.collect()
        DEBUG("Garbage collect returned %i"%count)

        end_tick_time = utcnow()
        total_tick_time = (end_tick_time - start_tick_time).total_seconds()
        INFO("Done tick. Total tick time: %.2f"%(total_tick_time))


    #########################
    # Execute the tick
    def processIncidents(self, incidents, curTime, tickDelta = 0.0):
        """
        - Compare the current list of ELES incidents to those we have in the database.
        - Add any new units or symptom codes that we are seeing for the first time.
        - Update the database with new statuses.
        - Return a list of units that have changed status as list of tuples:
            (unit_id, unit, old_status, new_status, key_status)
            old_status and new_status are instances of models.UnitStatus
            key_status is an instance of models.KeyStatuses
        """

        # Add any units or symptom codes that we are seeing for the first time.
        # If we are seeing a unit for the first time,
        # an initial operational status will be created for the unit.
        for inc in incidents:
            update_db_from_incident(inc, curTime)

        symptoms = list(SymptomCode.objects)
        symptom_description_to_symptom = dict((s.description, s) for s in symptoms)

        # units = list(Unit.objects)
        # unit_id_to_unit = dict((u.unit_id, u) for u in units)

        unit_id_to_incident = dict((i.UnitId, i) for i in incidents)

        # Make a dictionary of unit id to the new status
        unit_to_new_symptom_desc = dict((i.UnitId, i.SymptomDescription) for i in incidents)
        outage_units = set(unit_to_new_symptom_desc.keys())

        unit_id_to_old_symptom_desc = dict((unit.unit_id, unit.key_statuses.lastStatus.symptom_description) for unit in Unit.objects.no_cache())

        DEBUG("Running garbage collector after iteration over units.")
        count = gc.collect()
        DEBUG("Garbage collect returned %i"%count)


        was_not_operationals = set(unit_id for unit_id, symptom_desc in unit_id_to_old_symptom_desc.iteritems() if \
                             symptom_desc != "OPERATIONAL")

        was_operationals = set(unit_id for unit_id, symptom_desc in unit_id_to_old_symptom_desc.iteritems() if \
                             symptom_desc == "OPERATIONAL")

        now_out = was_operationals.intersection(outage_units)
        now_on = was_not_operationals.difference(outage_units)
        still_out = was_not_operationals.intersection(outage_units)

        # Determine those units that have changed status. There are several cases:

        # 1. We are seeing a unit for the first time. It appears in the incidents list
        # as an outage but we do not have a record if it in the database.
        # 2. A unit appears on the incidents list as an outage, but our last record of it
        # was operational. This is a new outage.
        # 3. A unit appears on the incidents list as an ouatage, and our last record of it 
        # was an outage. The symtpom descriptions match, so nothing has changed.
        # 4. A unit appears on the incidents list as an outage, and our last record of it 
        # was an outage, but the symptom descriptions to not match.
        #   - For example, this could be a transition from an inspection to a minor repair.
        # 5. Our last record for a unit was an outage, but it is no longer on the incidents list.
        # This means the unit has been repaired.

        changed_statuses = []
        changed_unit_ids = []


        # Add units that were operational that are no longer
        changed_unit_ids.extend(now_out)

        # Add units that were not operational that now are
        changed_unit_ids.extend(now_on)

        # Add units that still aren't operational but have change status.
        changed_unit_ids.extend(unit_id for unit_id in still_out if \
                unit_to_new_symptom_desc[unit_id] != unit_id_to_old_symptom_desc[unit_id] )

        changed_units = []

        for unit_id in changed_unit_ids:
            unit = Unit.objects.no_cache().get(unit_id = unit_id)
            key_status = unit.key_statuses
            old_status = key_status.lastStatus
            old_status._add_timezones()

            # If we have an incident, grab the symptom code.
            # Otherwise the unit is operational.
            incident = unit_id_to_incident.get(unit_id, None)
            if incident:
                symptom_description = incident.SymptomDescription
            else:
                symptom_description = "OPERATIONAL"

            # Save new UnitStatus
            symptom = symptom_description_to_symptom[symptom_description]
            new_status = UnitStatus(unit = unit, 
                                        time = curTime,
                                        tickDelta = tickDelta,
                                        symptom = symptom)
            new_status.denormalize()
            new_status.save()

            changed_units.append((unit_id, unit, old_status, new_status, key_status))

            DEBUG("Running garbage collector after saving new status.")
            count = gc.collect()
            DEBUG("Garbage collect returned %i"%count)

            

        return changed_units

    ############################################################
    # Generate tweet msgs using the changedStatusDict
    # Return a list of tweets
    def generate_tweets(self, changed_statuses, url_maker=None):

        if not changed_statuses:
            return []

        # Extend a tweet by appending another string only if it doesnt violate
        # tweet legnth
        def extend_tweet(msg1, msg2):
            if not msg2:
                return msg1
            newmsg = '%s %s'%(msg1, msg2)
            return newmsg if len(newmsg) <= TWEET_LEN else msg1

        def extend_tweet_url(msg1, url):
            newL = len(msg1) + 23 # 22 for url, plus space
            newmsg = '%s %s'%(msg1, url)
            return newmsg if newL <= TWEET_LEN else msg1

        tweet_msgs = []

        for (unit_id, unit, old_status, new_status, key_status) in changed_statuses:

            new_symptom = new_status.symptom_description
            new_symptom_category = new_status.symptom_category

            last_symptom = old_status.symptom_description
            last_symptom_category = old_status.symptom_category

            # Get 6 char escalator code
            station_code = unit.station_code
            unit_id_sort = unit_id[0:6]
            station_short_name = stations.codeToShortName[station_code]

            tweet_msg = ''

            if last_symptom_category == 'ON':

                # This unit has broken, or turned off
                pfx = 'Off' if new_symptom_category != 'BROKEN' else 'Broken'
                tweet_msg = '{pfx}! #{station} #{unit}. Status is {symptom}.'
                tweet_msg = tweet_msg.format(pfx=pfx,
                                           station=station_short_name,
                                           unit=unit_id_sort,
                                           symptom=new_symptom)

                if new_symptom_category == 'BROKEN':

                    # Add to tweet "Last broke X days ago."
                    last_fix = key_status.lastFixStatus
                    if last_fix:
                        time_since_fix = (new_status.time - old_status.time).total_seconds()
                        last_broke_str = make_last_broke_str(time_since_fix)
                        tweet_msg = extend_tweet(tweet_msg, last_broke_str)

            elif new_symptom_category == 'ON':

                # This unit is back online. It either represents a transition from a broken state,
                # turned off state, or inspection state.

                # This unit has broken, or turned off
                pfx = 'Fixed' if last_symptom_category == 'BROKEN' else 'On'
                tweet_msg = '{pfx}! #{station} #{unit}. Status was {symptom}.'
                tweet_msg = tweet_msg.format(pfx=pfx,
                                           station=station_short_name,
                                           unit=unit_id_sort,
                                           symptom=last_symptom)

                # Get the downtime
                last_op = key_status.lastOperationalStatus
                last_op._add_timezones()
                last_op_time = last_op.end_time
                if last_op_time:
                    down_time = (new_status.time - last_op_time).total_seconds()
                    down_time_str = time_str_compact(down_time)
                    tweet_msg = extend_tweet(tweet_msg, 'Downtime %s'%down_time_str)
            else:
                # This represents a transition between non-operational states. Tweet
                # about the updated state change
                tweet_msg = 'Updated: #{station} #{unit} was {symptom1}, now {symptom2}.'
                tweet_msg = tweet_msg.format(station=station_short_name, unit=unit_id_sort,
                                           symptom1 = last_symptom,
                                           symptom2 = new_symptom)


            # Tack on url string
            if url_maker:
                urlStr = url_maker(unit_id)
                if urlStr:
                    tweet_msg = extend_tweet_url(tweet_msg, urlStr)

            tweet_msgs.append(tweet_msg)

        return tweet_msgs

    def broadcast_tweets(self, units, tweets):

        for u, t in zip(units, tweets):
            
            T = self.escTwitter if u.is_escalator() else self.eleTwitter

            try:

                T.PostUpdate(t)

            except TwitterError as e:
                logger.error('Caught TwitterError when trying to tweet.\n\tmsg: %s\m\t%s'%(t, str(e)))
            

####################################################
# Convert seconds to a time string
# example: 160 minutes = "2 hrs, 40 min."
def time_str(sec):
    timeStr = ''
    if sec > 60:
        days = int(sec / (60*60*24))
        rem = int(sec - (60*60*24)*days)
        hr = int(rem / (60*60))
        rem = int(rem - hr*(60*60))
        mn = int(rem/60)
        timeStr = []
        if days > 0:
            sfx = 'days' if days > 1 else 'day'
            timeStr.append('%i %s'%(days, sfx))
        if hr > 0:
            sfx = 'hrs' if hr > 1 else 'hr'
            timeStr.append('%i %s'%(hr,sfx))
        if mn > 0:
            timeStr.append('%i min'%mn)
        timeStr = ', '.join(timeStr)
    return timeStr

####################################################
# Convert seconds to a string of the form HH:mm
def time_str_compact(sec):
    time_str = ''
    hrs = int(sec/3600.0)
    rem = sec - hrs*3600
    minutes = int(rem/60.0)
    rem = rem - 60*minutes
    if hrs > 0:
        time_str = '{hr:0>2d}h{min:0>2d}m'.format(hr=hrs,min=minutes)
    else:
        time_str = '{min:0>2d}m'.format(hr=hrs,min=minutes)
    return time_str

###################################################
def make_last_broke_str(secs):
    if secs is None:
        return ''
    if secs <= 0:
        return ''
    sec_per_day = 3600*24.0
    last_broke_days = int(secs/sec_per_day)
    if last_broke_days == 0:
        lastBrokeStr = 'Last broke earlier today.'
    elif last_broke_days == 1:
        lastBrokeStr = 'Last broke yesterday.'
    elif last_broke_days > 1:
        lastBrokeStr = 'Last broke %i days ago.'%last_broke_days
    return lastBrokeStr
