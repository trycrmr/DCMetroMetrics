import pymongo
from keys import HotCarKeys as keys
from twitter import TwitterError
import twitterUtils
import sys
import re
from datetime import datetime, timedelta
from dateutil import tz
import time
from collections import defaultdict
import dbUtils


ME = 'MetroHotCars'.upper()

##########################
def initAppState(db, curTime):
    if db.hotcars_appstate.count() == 0:
        doc = {'_id' : 1,
               'lastRunTime' : curTime,
               'lastTweetId' : 0}
        db.hotcars_appstate.insert(doc)

    if db.hotcars_tweeters.count() == 0:
        initTweetersDB(db)

##########################
def initTweetersDB(db, log=sys.stderr):

    # initialize the database of tweeters
    hotcarTweetIds = db.hotcars.distinct('tweet_id')
    tweeterIds = []
    for tweetId in hotcarTweetIds:
        rec = next(db.hotcars_tweets.find({'_id': tweetId}))
        tweeterId = rec['user_id']
        tweeterIds.append(tweeterId)
    log.write('Looking up %i tweetIds\n'%len(tweeterIds))

    T = getTwitterAPI()
    numAdded = 0
    for twitterId in tweeterIds:
        res = T.get_user(twitterId)
        handle = res.screen_name
        doc = {'_id' : twitterId,
               'handle' : handle}
        if db.hotcars_tweeters.find({'_id' : twitterId}).count() == 0:
            db.hotcars_tweeters.insert(doc)
            numAdded += 1
    log.write('Added %i docs to hotcars_tweeters collection\n'%numAdded)

#########################################
# Get all hot car reports for a given car
def getHotCarReportsForCar(db, carNum):
    carNumStr = str(carNum)
    query = {'car_number' : carNumStr}
    db.hotcars.ensure_index([('car_number',pymongo.ASCENDING),('time', pymongo.DESCENDING)])
    cursor = db.hotcars.find(query).sort('time', pymongo.DESCENDING)
    hotCarReports = list(cursor)
    # Convert times to local time
    for rec in hotCarReports:
        rec['time'] = UTCToLocalTime(rec['time'])
        rec['tweet_id'] = int(rec['tweet_id'])
        rec['user_id'] = int(rec['user_id'])
    return hotCarReports

#########################################
# Add text, user_id, handle fields to the record
def makeFullHotCarReport(db, rec):
    tweetId = rec['tweet_id']
    tweetRec = db.hotcars_tweets.find_one({'_id' : tweetId})
    rec['user_id'] = tweetRec['user_id']
    rec['text'] = tweetRec['text']
    tweeterRec = db.hotcars_tweeters.find_one({'_id' : tweetRec['user_id']})
    rec['handle'] = tweeterRec['handle']
    rec['car_number'] = int(rec['car_number'])
    rec['tweet_id'] = int(rec['tweet_id'])
    rec['user_id'] = int(rec['user_id'])
    return rec
     

#########################################
def getAllHotCarReports(db):
    db.hotcars.ensure_index([('car_number',pymongo.ASCENDING),('time', pymongo.DESCENDING)])
    cursor = db.hotcars.find().sort('time', pymongo.DESCENDING)
    hotCarReportDict = defaultdict(list)
    for report in cursor:
        report = makeFullHotCarReport(db, report)
        # Convert the time stamp to local time
        report['time'] = UTCToLocalTime(report['time'])
        hotCarReportDict[report['car_number']].append(report)
    return hotCarReportDict

##########################
# Preprocess tweet text by padding 4 digit numbers with spaces,
# and converting all characters to uppercase
def preprocessText(tweetText):
    tweetText = tweetText.encode('ascii', errors='ignore')
    tweetText = tweetText.upper()

    # Remove any handles that are not @WMATA, to avoid mistaking
    # a 4 digit number in handle as a car number
    words = tweetText.split()
    words = [w for w in words if (w[0] != '@') or (w == '@WMATA')]
    tweetText = ' '.join(words)

    # Replace alphanumerica characters with spaces
    tweetText = re.sub('[^a-zA-Z0-9\s]',' ', tweetText)

    # Separate numbers embedded in words
    tweetText = re.sub('(\d+)', ' \\1 ', tweetText)

    # Make consecutive white space a single space
    tweetText = re.sub('\s+', ' ', tweetText)

    # Remove reference to 1000, 2000, ..., 6000 Series
    tweetText = re.sub('[1-6]000 SERIES', '', tweetText)
    return tweetText



###########################
# Get 4 digit numbers
def getCarNums(text):
    nums = re.findall('\d+', text)
    validNums = list(set(s for s in nums if len(s)==4))
    return validNums

#######################################
# Get colors mentioned from a tweet
def getColors(text):
    colorToWords = { 'RED' : ['RD', 'RL'],
                     'BLUE' : ['BL'],
                     'GREEN' : ['GR', 'GL'],
                     'YELLOW' : ['YL'],
                     'ORANGE' : ['OL'],
                     #'SILVER' : ['SL']
                   }
    for c in colorToWords:
        colorToWords[c].append(c)
    wordToColor = dict((w,k) for k,wlist in colorToWords.iteritems() for w in wlist)
    colors = (wordToColor.get(w, None) for w in text.split())
    colors = [c for c in colors if c is not None]
    return colors

T = None
def getTwitterAPI():
    global T
    if T is None:
        T = twitterUtils.getApi(keys)
    return T

###########################
# Return a list of unique tweets from a list of tweets
def uniqueTweets(tweetList):    
    seen = set()
    unique = [t for t in tweetList if not (t.id in seen or seen.add(t.id))]
    return unique

############################################
# Get tweets which have been manually tagged as hot
# car reports. These tweets are in the hotcars_manual_tweets collection
def getManuallyTaggedTweets(db, log=sys.stderr):
    if db.hotcars_manual_tweets.count() == 0:
        return []
    docs = list(db.hotcars_manual_tweets.find())
    tweetIds = [d['_id'] for d in docs]
    tweets = []
    for tid in tweetIds:
        T = getTwitterAPI()
        try:
            tweet = T.GetStatus(tid, include_entities=True)
            tweets.append(tweet)
            log.write('Got manual weet %i! Removing from hotcars_manual_tweets\n'%tid)
            db.hotcars_manual_tweets.remove({'_id' : tid})
        except TwitterError as e:
            log.write('Caught TwitterError when trying to get manually tagged tweet %i: %s\n'%(tid, str(e)))
    return tweets

#######################################
def tick(db, tweetLive = False, log=sys.stderr):
    curTime = datetime.now()
    log.write('Running HotCar Tick. %s\n'%(str(curTime)))
    log.write('Tweeting Live: %s\n'%str(tweetLive))
    log.flush()             
    initAppState(db, curTime)
    appState = next(db.hotcars_appstate.find({'_id' : 1}))
    lastTweetId = appState.get('lastTweetId', 0)

    T = getTwitterAPI()

    # Determine the last hot car tweet we saw, so we can search for
    # tweets that have occurred since
#    lastTweetId = 0
#    if db.hotcars_tweets.count() > 0:
#        sortParams = [('_id', pymongo.DESCENDING)]
#        doc = next(db.hotcars_tweets.find(sort=sortParams))
#        lastTweetId = doc['_id']
#
    log.write('last tweet id: %i\n'%lastTweetId)
#    lastTweetId = 0
#    log.write('Forcing last tweet id: %i\n'%lastTweetId)

    # Generate reponse tweets for any tweets which have not yet been acknowledged
    tweetResponses = []
    unacknowledged = list(db.hotcars_tweets.find({'ack' : False}))
    log.write('Found %i unacknowledged tweets\n'%(len(unacknowledged)))
    for doc in unacknowledged:
        tweetText = doc['text']
        user_id = doc['user_id']
        tweeterDoc = db.hotcars_tweeters.find_one({'_id': user_id})
        if tweeterDoc is None:
            log.write('Warning: Could not acknowledge unacknowledged tweet %i because user %s could not be found\n'%(doc['_id'], user_id))
            continue
        response = genResponseTweet(tweeterDoc['handle'], getHotCarData(tweetText))
        if response is not None:
            tweetResponses.append((doc['_id'], response))

    # Get the latest tweets about WMATA hotcars
    queries = ['wmata hotcar', 'wmata hot car', 'wmata hotcars', 'wmata hot cars']
    tweets = []
    for q in queries:
        res = T.GetSearch(q, count=100, since_id = lastTweetId, result_type='recent', include_entities=True)
        tweets.extend(t for t in res)

    # Get tweets which have been manually curated
    tweets.extend(getManuallyTaggedTweets(db))
    tweets.extend(getMentions(curTime=curTime))
    tweets = uniqueTweets(tweets)
    log.write('Twitter search returned %i unique tweets\n'%len(tweets))

    def filterPass(t):

        # Reject retweets
        if t.retweeted_status:
            return False

        # Ignore tweets from self
        if t.user.screen_name.upper() == ME.upper():
            return False

        return True

    filteredTweets = [t for t in tweets if filterPass(t)]
    tweetIds = set(t.id for t in filteredTweets)
    assert(len(tweetIds) == len(filteredTweets))

    log.write('Filtered to %i tweets after removing re-/self-tweets\n'%len(filteredTweets))

    tweetData = [(t,getHotCarData(t.text)) for t in filteredTweets]
    tweetData = [(t,hcd) for t,hcd in tweetData if tweetIsValid(t, hcd)]
    tweetData = filterDuplicates(tweetData)

    log.write('Filtered to %i tweets after removing invalid and duplicate reports\n'%len(tweetData))
    log.write('Have %i tweets about hot cars\n'%len(tweetData))

    for tweet, hotCarData in tweetData:
        validTweet = tweetIsValid(tweet, hotCarData)
        if not validTweet:
            continue

        updated = updateDBFromTweet(db, tweet, hotCarData)

        # If we updated the database with data on this tweet,
        # generate a response tweet
        if updated:
            user = tweet.user.screen_name
            response = genResponseTweet(user, hotCarData)
            if response is not None:
                tweetResponses.append((tweet.id, response))

    # Update the app state
    maxTweetId = max([t.id for t in filteredTweets]) if filteredTweets else 0
    maxTweetId = max(maxTweetId, lastTweetId)
    update = {'$set' : {'lastRunTime': curTime, 'lastTweetId' : maxTweetId}}
    query = {'_id' : 1}
    db.hotcars_appstate.update(query, update, upsert=True)

    # Tweet Responses
    for tweetId, response in tweetResponses:
        if response is None:
            continue
        log.write('Response for Tweet %i: %s\n'%(tweetId, response))
        if tweetLive:
            try:
                T.PostUpdate(response, in_reply_to_status_id = tweetId)

                # Update the acknowledgement status of the tweet
                query = {'_id' : tweetId}
                update = {'$set' : {'ack' : True}}
                db.hotcars_tweets.find_and_modify(query=query, update=update)
            except TwitterError as e:
                log.write('Caught TwitterError!: %s'%str(e))

########################################
# Get hot car data from a tweet
def getHotCarData(text):
    pp = preprocessText(text)
    carNums = getCarNums(pp)
    colors = getColors(pp)
    return {'cars' : carNums,
            'colors' : colors }

##################################################
# Get the UTC time of the tweet, from sec since epoch
# in localtime. The epoch is midnight 1/1/1970 in UTC.
def makeUTCDateTime(secSinceEpoch):
    t = time.gmtime(secSinceEpoch)
    dt = datetime(t.tm_year, t.tm_mon, t.tm_mday,
                  t.tm_hour, t.tm_min, t.tm_sec)
    return dt

##################################################
# Convert UTC datetime to local datetime
def UTCToLocalTime(utcDateTime):
    from_zone = tz.tzutc()
    to_zone = tz.tzlocal()
    utcdt = utcDateTime.replace(tzinfo=from_zone)
    localdt = utcdt.astimezone(to_zone)
    return localdt

########################################
def updateDBFromTweet(db, tweet, hotCarData, log=sys.stderr):

    log.write('hotcars_tweets has %i docs\n'%(db.hotcars_tweets.count()))
    log.write('Updating hotcar_tweets collection with tweet %i\n'%tweet.id)
    tweetText = tweet.text.encode('utf-8', errors='ignore')

    # Check if this is a duplicate, and abort if so.
    count = db.hotcars_tweets.find({'_id' : tweet.id}).count()
    if count > 0:
        log.write('No update made. Tweet %i is a duplicate.\n'%tweet.id)
        updated = False
        return updated

    # Store the tweet
    doc = {'_id' : tweet.id,
           'user_id' : tweet.user.id,
           'text' : tweetText,
           'time' : makeUTCDateTime(tweet.created_at_in_seconds),
           'ack' : False}
    db.hotcars_tweets.insert(doc)
    updated = True

    # Store the twitter user information
    update = {'_id' : tweet.user.id,
           'handle' : tweet.user.screen_name}
    query = {'_id': tweet.user.id}
    db.hotcars_tweeters.find_and_modify(query=query, update=update, upsert=True)

    # Update hotcars collection
    log.write('hotcars has %i docs\n'%(db.hotcars.count()))
    carNums = hotCarData['cars']
    colors = hotCarData['colors']
    carNum = int(carNums[0])
    color = colors[0] if colors and len(colors)==1 else 'NONE'
    doc = {'tweet_id' : tweet.id,
           'car_number' : carNum,
           'color' : color,
           'time' : makeUTCDateTime(tweet.created_at_in_seconds)}
    count = db.hotcars.find({'tweet_id' : tweet.id}).count()
    if count == 0:
        log.write('Updating hotcars collection with tweet %i\n'%tweet.id)
        db.hotcars.insert(doc)
    else:
        log.write('Not updating hotcars collection with tweet %i. Entry already exists!\n'%tweet.id)

    # Trigger regeneration of the hot car webpage
    db.webpages.find_and_modify({'class' : 'hotcars'}, {'$set' : {'forceUpdate' : True}})

    return updated

###########################################################
# Return True if we should store hot car data on this tweet
# and generate a twitter response
def tweetIsValid(tweet, hotCarData):

    # Ignore tweets from self
    me = 'MetroHotCars'
    if tweet.user.screen_name.upper() == me.upper():
        return False

    # Ignore retweets
    if tweet.retweeted_status is not None:
        return False

    carNums = hotCarData['cars']

    # Require a single car to be named in the tweet
    if len(carNums) != 1:
        return False

    carNumStr = str(carNums[0])
    carNumInt = int(carNums[0])
    firstDigit = carNumStr[0]


    # Car ranges are from Wikipedia, inclusive
    carRanges = { '1' : (1000, 1299),
                  '2' : (2000, 2075),
                  '3' : (3000, 3289),
                  '4' : (4000, 4099),
                  '5' : (5000, 5191),
                  '6' : (6000, 6183)
                }

    if firstDigit not in carRanges:
        return False

    # Require the car to be a valid number
    minNum, maxNum = carRanges[firstDigit]
    carNumValid = carNumInt <= maxNum and carNumInt >= minNum
    if not carNumValid:
        return False

    # Check if the tweet has any forbidded words
    excludedWords = ['series'] # People may refer to 3000 series cars
    excludedWords = [w.upper() for w in excludedWords]
    excludedWords = set(excludedWords)
    tweetText = preprocessText(tweet.text)
    tweetWords = tweetText.split()
    numExcluded = sum(1 for w in tweetWords if w in excludedWords)
    if numExcluded > 0:
        return False

    # The tweet is good!
    return True


#########################################
# Remove tweets which are duplicate reports.
# A tweet is a duplicate report if:
#
# 1. It mentions another user
# who previously reported the same hot car within the last
# 30 days.
#
# 2. The same user reported the same hot car within the last
# 30 days.
#
# tweetData: List of (tweet, hotCarData) tuples
def filterDuplicates(tweetData, log=sys.stderr):

    # Build a dictionary from car number to the reporting users/times
    db = dbUtils.getDB()
    hotCarReportDict = getAllHotCarReports(db)

    # Add the current batch of tweets to the hotCarReportDic
    for tweet, hotCarData in tweetData:
        cars = hotCarData['cars']
        assert(len(cars)==1)
        carNumber = cars[0]
        data = {'car_number' : carNumber,
                'time' : datetime.fromtimestamp(tweet.created_at_in_seconds),
                'tweet_id' : tweet.id,
                'user_id' : tweet.user.id
               }                
        hotCarReportDict[carNumber].append(data)

    filteredTweetData = []
    for tweet, hotCarData in tweetData:
        tweetTime = datetime.fromtimestamp(tweet.created_at_in_seconds)
        timeCutoff = tweetTime - timedelta(days=30)
        user_id = tweet.user.id
        screen_name = tweet.user.screen_name
        tweet_id = tweet.id
        assert(len(cars)==1)
        carNumber = hotCarData['cars'][0]
        otherReports = [d for d in hotCarReportDict[carNumber] if d['tweet_id'] != tweet_id and
                                                                  d['time'] > timeCutoff]

        # Check if this car was already reported by the user within 30 days
        prevSelfReports = sum(1 for d in otherReports if d['user_id'] == user_id)
        
        # Check if this car was already reported by another user mentioned in this tweet
        mentionUserIds = set(u.id for u in tweet.user_mentions)
        if tweet.in_reply_to_user_id is not None:
            mentionUserIds.add(tweet.in_reply_to_user_id)

        otherUserReports = sum(1 for d in otherReports if (d['user_id'] in mentionUserIds)
                                                          and (d['time'] < tweetTime))
        if (prevSelfReports > 0):
            log.write('Skipping Tweet %i by %s on car %i because there is a previous self-report' \
                      ' in last 30 days\n'%(tweet_id, carNumber, screen_name))
            continue
        if (otherUserReports > 0):
            log.write('Skipping Tweet %i by %s on car %i because it mentions another report ' \
                      ' from last 30 days\n'%(tweet_id, carNumber, screen_name))
            continue
        filteredTweetData.append((tweet, hotCarData))

    return filteredTweetData

########################################
# Generate reponse tweet
def genResponseTweet(toScreenName, hotCarData):
    carNums = hotCarData['cars']
    colors = hotCarData['colors']
    tweetValid = len(carNums)==1 and carNums[0][0] in '123456'
    if not tweetValid:
        return None

    normalize = lambda s: s[0].upper() + s[1:].lower()
    user = toScreenName
    color = normalize(colors[0]) if len(colors) == 1 else ''
    car = carNums[0]
    if color:
        msg = '@wmata {color} line car {car} is a #wmata #hotcar HT @{user}'.format(color=color, car=car, user=user)
    else:
        msg = '@wmata Car {car} is a #wmata #hotcar HT @{user}'.format(car=car, user=user)
    return msg

#######################################
# Get tweets which mention MetroHotCars
# since this is rate limited to once per minute,
# only do this once per 90 sec
def getMentions(curTime):
    db = dbUtils.getDB()
    appState = next(db.hotcars_appstate.find({'_id' : 1}))
    lastMentionsCheckTime = appState.get('lastMentionsCheckTime', None)
    lastTweetId = appState.get('lastTweetId', 0)
    doCheck = False

    if (lastMentionsCheckTime is None) or \
       (curTime - lastMentionsCheckTime) > timedelta(seconds=90.0):
       doCheck = True

    if not doCheck:
        return []
    
    T = getTwitterAPI() 
    mentions = T.GetMentions(include_entities=True, since_id=lastTweetId)

    # Update the appstate
    update = {'lastMentionsCheckTime' : curTime}
    db.hotcars_appstate.update({'_id' : 1}, {'$set' : update})
    return mentions


