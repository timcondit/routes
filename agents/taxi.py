#!/usr/bin/env python
'''DOCSTRING'''

import math
from operator import itemgetter # itemgegger is new in Python 2.4
from random import randint, seed, choice
from agent import Agent
import ConfigParser
import os.path

config = ConfigParser.SafeConfigParser()
config.read(os.path.join('agents', 'defaults.ini'))
config.read(os.path.join('agents', 'overrides.ini'))

# dev config values
TRACING = config.getboolean('dev', 'tracing')
DEBUG = config.getboolean('dev', 'debug')
SET_TRACE = config.getboolean('dev', 'setTrace') # for interactive debugging

# runtime config values
SIMTIME = config.getint('runtime', 'simulationTime')
TAXI_RANGE_LOW = config.getfloat('runtime', 'taxiRangeLow')
TAXI_RANGE_MID = config.getfloat('runtime', 'taxiRangeMedium')
TAXI_RANGE_HI = config.getfloat('runtime', 'taxiRangeHigh')
GRID_MIN = config.getint('runtime', 'gridMin')
GRID_MAX = config.getint('runtime', 'gridMax')
SIMTYPE = config.get('runtime', 'simType')

if TRACING:
    from SimPy.SimulationTrace import *
else:
    from SimPy.Simulation import *

if SET_TRACE:
    import pdb

# This is used by the filter functions.  Looks like (23, 61).
taxi_loc = ()


class Taxi(Agent):
    '''DOCSTRING'''
    def __init__(self, name, np): # negotiation protocol
        Agent.__init__(self, name)
        self.np=np

	# Once a Fare is lost, we can no longer compete for it.  The flip side
	# is that this queue is complementary to the targetFare.competeQ, so
	# maybe it should be called the non- or noCompeteQ.  
	self.lostFaresQ=[]
        print '%.4f Taxi %s activated' % (self.ts['activation'], self.name)
        print '.. Taxi %s location: %s' % (self.name, self.loc)

    def cooperate(self):
        '''
Coordinate pickups with other Taxis.  A SimPy PEM.

This is the PEM for cooperative negotiation.  In this simulation, Taxis choose
a Fare for pickup, and take a reference to the Fare at acknowledgment.  The
Taxi effectively locks out other Taxis from competing for that Fare by
removing it from the queue of waiting Fares just before yield'ing for the ride
to the Fare.

Contrast with the compete() method.
        '''
        global taxi_loc
        while True:
            if len(Agent.waitingFares.theBuffer) > 0:
                if DEBUG:
                    print
                    print '.. Taxi %s is looking for an eligible fare:' % self.name
                    print '.. waitingFares (pre) ', [x.name for x in Agent.waitingFares.theBuffer]
                taxi_loc = self.loc['curr']

                # Choose a Fare
                if self.np == 'FIFO':
                    yield get, self, Agent.waitingFares, 1
                elif self.np == 'closestfare':
                    numAgents=len(Agent.waitingFares.theBuffer)
                    yield get, self, Agent.waitingFares, closestfare_cooperate
                    #yield get, self, Agent.waitingFares, self.closestfare_cooperate
                elif self.np == 'mixedmode':
                    yield get, self, Agent.waitingFares, mixedmode_cooperate
                    #yield get, self, Agent.waitingFares, self.mixedmode_cooperate
                else:
                    print 'Something broke in the negotiation protocol!'
                if DEBUG:
                    print '.. waitingFares (post)', [x.name for x in Agent.waitingFares.theBuffer]
                    assert len(self.got) == 1
                print '%.4f Taxi %s chose Fare' % (now(), self.name), [x.name for x in self.got]

# Found this line in an old printout that may be "newer" than this code.  It's
# redundant in the unified project.
#                print 'taxi_loc before', taxi_loc
#                print 'setting old printout code ...'
#                taxi_loc = self.loc['curr']
#                print 'taxi_loc after', taxi_loc

                fareBeingDriven=self.got[0]

                # Drive to Fare
                #drive_dist = getdistance(fareBeingDriven.loc['curr'], taxi_loc)
                drive_dist=self.map.get_distance(fareBeingDriven.loc['curr'], taxi_loc)

                if DEBUG:
                    print '%.4f Taxi %s driving to Fare %s' % (now(), self.name,
                            fareBeingDriven.name)
                yield hold, self, drive_dist

                # Pick up Fare
                self.loc=fareBeingDriven.loc     # tuple
                if DEBUG:
                    print '%.4f Taxi %s arrives to pick up Fare %s' % (now(), self.name,
                            fareBeingDriven.name)

                # Drive to Fare's destination
                drive_dist=self.map.get_distance(self.loc['dest'], self.loc['curr'])

                if DEBUG:
                    print "%.4f Taxi %s driving to Fare %s's destination" % (now(), self.name,
                            fareBeingDriven.name)
                yield hold, self, drive_dist

                # Drop off Fare
                self.loc['curr'] = fareBeingDriven.loc['dest']

# @@ This one was NOT found in the old printout referred to above.
                self.loc['dest'] = ()

                if DEBUG:
                    print '%.4f Taxi %s dropping off Fare %s' % (now(), self.name,
                            fareBeingDriven.name)
                fareBeingDriven.doneSignal.signal(self.name)
            else:
                print '%.4f INFO: %s: There are no eligible Fares for this Taxi.' % (now(),
                        self.name)

                # Throttle back the flood of messages.
                #
                # NOTE: I'm using a simple 'yield hold <small-number>' here
                # because there are two main events that can occur, and the
                # Taxi should be able to respond to them promptly.  The first
                # is the arrival of a new Fare into the buffer.  The second is
                # a Fare already in the queue that becomes eligible for
                # inspection by the Taxi.
                yield hold, self, 2


    def compete(self):
        '''
Compete for Fares against other Taxis.  A SimPy PEM.

This method differs from cooperate() in a couple important ways.  First is
that the Taxis are currently competing for Fares in an "every man for himself"
sort of way.  Second is that compete uses the same negotiation protocols, but
they work a little differently.  Instead of taking a reference to a Fare, and
removing it from the queue of eligible Fares as soon as it is identified,
these Taxis need to reach the Fare before they can claim it as their own.
Also, the Taxis do not communicate with each other during the competition.
They mainly communicate with each other when one of them claims a Fare, and
alerts the others that that Fare is no longer available.

Implementation detail: Every Taxi that competes for a particular Fare goes
into that Fare's competeQ.  Then the Taxi yields for drive_dist time.  If the
yield expires, that Taxi got there first, and wins the Fare!  The winning Taxi
pulls the Fare from the queue, and interrupts all other members of that Fare's
competeQ.   The remaining Taxis call self.interruptReset(), and go off to
compete for another Fare.

Because the Fares stay in the queue until someone "wins" the right to go pick
them up, latecomers should have a shot at them too.  If they are closer, they
may get the Fare, even though others are already competing for that Fare.

Contrast with the cooperate() method.
        '''
        global taxi_loc
        while True:
#            print 'current list of waitingFares', [x.name for x in self.waitingFares.theBuffer]
            if len(Agent.waitingFares.theBuffer) > 0:
                if DEBUG:
                    my_curr_pre = self.loc['curr']
                    my_dest_pre = self.loc['dest']

                # Choose a Fare
                if self.np=='FIFO':
                    targetFare=Agent.waitingFares.theBuffer[0]
                elif self.np=='closestfare':
                    numAgents=len(Agent.waitingFares.theBuffer)
		    # set a global variable here to use in closestfare_compete
		    taxi_loc=self.loc['curr']
                    targetFare=self.closestfare_compete()
#                    targetFare=closestfare_compete(Agent.waitingFares.theBuffer)
                    if DEBUG:
                        assert(numAgents==len(Agent.waitingFares.theBuffer))
                        print '%s targetFare closestfare %s' % (self.name, targetFare.name)
                elif self.np=='mixedmode':
                    numAgents=len(Agent.waitingFares.theBuffer)
                    targetFare=self.mixedmode_compete()
                    if DEBUG:
                        assert(numAgents==len(Agent.waitingFares.theBuffer))
                    if targetFare:
                        if DEBUG:
                            print '%s targetFare closestfare %s' % (self.name, targetFare.name)
                    else:
                        # Not a lot I can do here, since I don't have a Fare
                        # to compete for.  I should never get here, so this
                        # should be an Error.  For now I'll just throttle back
                        # the flood of messages and move on.
                        print '%.4f INFO: %s: There are no eligible Fares for this Taxi.' % \
                                (now(), self.name)
                        yield hold, self, 2
                        continue
		# End choose a Fare


                # When we get to this point, we've got a Fare.  If the NP is
		# closestfare_compete, it's a Fare that was not already picked
		# up.

                if DEBUG:
                    assert(self.loc['curr'] == my_curr_pre)
                    assert(self.loc['dest'] == my_dest_pre)

		if self not in targetFare.competeQ:
#                    assert not targetFare.pickedUp
                    targetFare.competeQ.append(self)
                    if DEBUG:
                        print ".. %.4f %s adding self to Fare %s's targetFare.competeQ" % \
					(now(), self.name, targetFare.name)

                # 
                self.loc['dest'] = targetFare.loc['curr']
                drive_dist=self.map.get_distance(self.loc['dest'], self.loc['curr'])
                self.driving_to_Fare=now()

                # Drive to Fare, try to get there first
                yield hold, self, drive_dist

                # If interrupted, another Taxi beat me to the Fare.
                if self.interrupted():
                    if DEBUG:
                        print '.. Taxi %s was interrupted by' % self.name,
                        print self.interruptCause.name, 'at %.4f' % now()
		        print 
                    self.interruptReset()

                    if DEBUG:
                        if targetFare.pickedUp:
                            print ".. %s.pickedUp" % targetFare.name
                        print "..  (pre) contents of %s's competeQ:" % targetFare.name,
                        for competitor in targetFare.competeQ:
                            print competitor.name,
                        print

		    # NEW TESTING -- add if DEBUG: if this works
                    print ".. %.4f %s removing self from Fare %s's targetFare.competeQ" % \
				    (now(), self.name, targetFare.name)
		    try:
                        targetFare.competeQ.remove(self)
                    except ValueError:
                        print "Taxi not found in %s's competeQ!" % targetFare.name
                        continue

                    # how the f*** am I going to get the other Taxis out of
		    # targetFare.competeQ?
                    if DEBUG:
                        print ".. (post) contents of %s's competeQ:" % targetFare.name,
                        for competitor in targetFare.competeQ:
                            print competitor.name,
                        print

                    # check if the Fare has been picked up since we yielded
		    # for the drive over
                    if targetFare.pickedUp:
                        print "Taxi %s's Fare (%s) has already been picked up" % \
					    (self.name, targetFare.name)
                    self.loc['curr']=Agent.map.update_location(self.loc['curr'],
                                    self.loc['dest'], now()-self.driving_to_Fare)
		    self.loc['dest']=()


		# Not interrupted, so I win!  Take the Fare from
		# waitingFares.theBuffer, and interrupt the Taxis who lost out
		# on this one.
		elif not self.interrupted():
                    # Pick up Fare
                    print '%.4f %s arrives to pick up Fare %s' % (now(), self.name,
                            targetFare.name)

		    # This is a super-important step.  This is where the Fare
		    # is removed from the SimPy Store (Agent.waitingFares).
		    #
		    # Late note: not only is it super-important, it's
		    # ultra-fucked.  This call to yield get just pops the
		    # first Fare off the queue, which is not necessarily the
		    # one I want to give up.  No no no no!  The one I want to
		    # give up is the one that comes from calling
		    # closestfare_compete for fuck's sake!  How did I miss
		    # this for so long?
#                    yield get, self, Agent.waitingFares, 1

                    # This is the dumbest thing ever.  It's a function that
		    # takes targetFare as input parameter, and returns the
		    # same targetFare.  The sole purpose of it is to have a
		    # filter function to use with the SimPy Store.
#		    print "get_my_fare(targetFare): " % get_my_fare([targetFare])

                    yield get, self, Agent.waitingFares, closestfare_compete
#                    yield get, self, Agent.waitingFares, get_my_fare
#		    print "self.got:", self.got
                    print 'current list of waitingFares', [x.name for x in self.waitingFares.theBuffer]

                    # ugly
		    assert len(self.got)==1
		    targetFare=self.got[0]
		    targetFare.pickedUp=True

                    # Tell the other Taxis that they lost
                    for competingTaxi in targetFare.competeQ:
                        # IMPORTANT: Do not interrupt self!  It's a tough bug
                        # to track down.
                        if self.name==competingTaxi.name:
                            continue
                        else:
                            self.interrupt(competingTaxi)
			    print '%.4f %s is interrupting %s [Fare %s]' % (now(), self.name,
					    competingTaxi.name, targetFare.name)

                    drive_dist = self.map.get_distance(self.loc['curr'], targetFare.loc['dest'])
		    print "[%s] Distance to Fare %s's destination: %.4f" % \
				    (self.name, targetFare.name, drive_dist)
                    yield hold, self, drive_dist

                    # TODO signal Fare
                    # Drop off Fare
                    self.loc['curr'] = self.loc['dest']
                    self.loc['dest'] = ()
#                    if DEBUG:
#                        print '%.4f Taxi %s dropping off Fare %s' % (now(), self.name,
#                                fareBeingDriven.name)
#                    fareBeingDriven.doneSignal.signal(self.name)

                else:
                    # I've never seen this (thank goodness)
                    print '!! RED ALERT !!'

            else:
                print '%.4f INFO: %s: There are no eligible Fares for this Taxi.' % (now(),
                        self.name)
                # Throttle back the flood of messages.
                #
                # NOTE: I'm using a simple 'yield hold <small-number>' here
                # because there are two main events that can occur, and the
                # Taxi should be able to respond to them promptly.  The first
                # is the arrival of a new Fare into the buffer.  The second is
                # a Fare already in the queue that becomes eligible for
                # inspection by the Taxi.
                yield hold, self, 2


#    def updateLocation(self):
#        '''
#Update the Taxi's current position.
#
#This method is normally only called from compete(), after a Taxi has been
#interrupted while en'route to a Fare.  The interruption means that another
#Taxi (the one doing the interrupting) got to the Fare first, and this Taxi
#needs to figure out where he is, so he can set his loc['curr'], and compete
#for the next Fare.
#
#Implementation detail: to keep things simple, I am just putting the Taxi near
#the halfway point between their former current location and their destination.
#
#NOTE: This method works under the assumption that the Taxi travels 1 unit of
#the grid for each tick of the simulation's clock.  This may eventually become
#a configuration setting, but it's low priority.
#        '''
##        print '%s self.loc:' % self.name, self.loc
#        assert(self.loc['curr'])
#        assert(self.loc['dest'])
#
#        curr_tmp = {}
#        curr_tmp['x'] = ((self.loc['curr'][0] + self.loc['dest'][0])/2)
#        curr_tmp['y'] = ((self.loc['curr'][1] + self.loc['dest'][1])/2)
#
#        self.loc['curr'] = (curr_tmp['x'], curr_tmp['y'])
#        self.loc['dest'] = ()
#        curr_tmp.clear()
#        return


#def closestfare_compete(not_a_magic_buffer=None):
#    '''
#    TODO UPDATE DOCSTRING
#
#Filter: return the Fare that is geographically closest to the calling Taxi.
#
#NOTE: The Fare is returned as a single-element list, because that (a list) is
#what SimPy's yield is expecting.  This is a filter function for the Store, and
#should not be called directly.  This is the second of the Taxi's three
#negotiation protocols.
#    '''
#    tmp=[]
#    if not not_a_magic_buffer:
#        not_a_magic_buffer=Agent.waitingFares.theBuffer
#    if DEBUG:
#        if buffer==Agent.waitingFares.theBuffer:
#            print 'the buffers are equal'
#        if buffer is Agent.waitingFares.theBuffer:
#            print 'the buffers are the same'
#    if not len(not_a_magic_buffer)>0:
#        print 'Buffer is empty!'
#        return
#    for fare in not_a_magic_buffer:
#        # This is part of it, but not the only part.  Sometimes the Taxi
#	    # has already chosen this Fare, although it's not going to get it.
#	    # This seems impossible on the face of it - the only time we
#	    # should ever invoke self.closestfare_compete() is when we're
#	    # first starting, or after an interrupt.  Regardless, there are
#	    # definitely times when one or more Taxis are interrupting others
#	    # to pick up Fares that have already been picked up.
##            if fare.pickedUp:
##                if DEBUG:
##		    print 'Fare %s has already been picked up.  %s is skipping.' % \
##				(fare.name, self.name)
##                continue
##            else:
##                d=self.map.get_distance(fare.loc['curr'], self.loc['curr'])
##                if DEBUG:
##                    print 'Distance from %s to Fare %s: %.4f' % (self.name, fare.name, d)
##                tmp.append((fare, d))
#
#        d=Agent.map.get_distance(fare.loc['curr'], self.loc['curr'])
##        if DEBUG:
##            print 'Distance from %s to Fare %s: %.4f' % (self.name, fare.name, d)
#        tmp.append((fare, d))
#
#    tmp2=sorted(tmp, key=itemgetter(1))
#    result=map(itemgetter(0), tmp2)[0]
#    # Critical difference between the competition and cooperative
#    # closestfares: cooperative cf returns the Fare, and removes it from
#    # waitingFares.theBuffer immediately.  Competition cf just returns the
#    # Fare, without taking it out of the buffer until later.  Note also,
#    # this method returns a single Fare, not (a list containing) a single
#    # Fare.
#    return [result]


    def closestfare_compete(self, not_a_magic_buffer=None):
        '''
        TODO UPDATE DOCSTRING

    Filter: return the Fare that is geographically closest to the calling Taxi.

    NOTE: The Fare is returned as a single-element list, because that (a list) is
    what SimPy's yield is expecting.  This is a filter function for the Store, and
    should not be called directly.  This is the second of the Taxi's three
    negotiation protocols.
        '''
        tmp=[]
        if not not_a_magic_buffer:
            not_a_magic_buffer=Agent.waitingFares.theBuffer
        if DEBUG:
            if buffer==Agent.waitingFares.theBuffer:
                print 'the buffers are equal'
            if buffer is Agent.waitingFares.theBuffer:
                print 'the buffers are the same'
        if not len(not_a_magic_buffer)>0:
            print 'Buffer is empty!'
            return
        for fare in not_a_magic_buffer:
            d=Agent.map.get_distance(fare.loc['curr'], self.loc['curr'])
#            if DEBUG:
#                print 'Distance from %s to Fare %s: %.4f' % (self.name, fare.name, d)
            tmp.append((fare, d))

        tmp2=sorted(tmp, key=itemgetter(1))
        result=map(itemgetter(0), tmp2)[0]
        # Critical difference between the competition and cooperative
        # closestfares: cooperative cf returns the Fare, and removes it from
        # waitingFares.theBuffer immediately.  Competition cf just returns the
        # Fare, without taking it out of the buffer until later.  Note also,
        # this method returns a single Fare, not (a list containing) a single
        # Fare.
        return result

def closestfare_compete(buf):
    '''
    TODO UPDATE DOCSTRING

Filter: return the Fare that is geographically closest to the calling Taxi.

NOTE: The Fare is returned as a single-element list, because that (a list) is
what SimPy's yield is expecting.  This is a filter function for the Store, and
should not be called directly.  This is the second of the Taxi's three
negotiation protocols.
    '''
    tmp=[]
    for fare in buf:
        print 'xxx', fare.loc['curr'], taxi_loc
        d=Agent.map.get_distance(fare.loc['curr'], taxi_loc)
#        if DEBUG:
#            print 'Distance from %s to Fare %s: %.4f' % (self.name, fare.name, d)
        tmp.append((fare, d))

    tmp2=sorted(tmp, key=itemgetter(1))
    result=map(itemgetter(0), tmp2)[0]
    # Critical difference between the competition and cooperative
    # closestfares: cooperative cf returns the Fare, and removes it from
    # waitingFares.theBuffer immediately.  Competition cf just returns the
    # Fare, without taking it out of the buffer until later.  Note also,
    # this method returns a single Fare, not (a list containing) a single
    # Fare.
    return result


    # Third of the Taxi's three negotiation protocols.  See notes in
    # ~/finalproject/agents/docs/daily_status/2007/04/08.txt
    def mixedmode_compete(self, not_a_magic_buffer=None):
        '''
Find the Fare with the lowest aggregate score.

The Taxi uses a combination of the Fare's time in the waitingFares buffer plus
their distance from the Taxi to determine which Fares to inspect.  If there
are any Fares in this list, then the one with the lowest score (cost) is
returned to the caller.

If the list is empty, in other words, if there are no Fares which meet the
time and space (distance) requirements of this Taxi, the Taxi goes into a
getQ, and stays there until at least one suitable Fare comes along.
Fortunately, there don't seem to be any restrictions from SimPy on when a Taxi
can get out of the queue.  It's just a matter of satisfying the filter
function.

NOTE: This method is explicitly not a SimPy filter function.  It behaves
similarly, but is used for competition only, which has different requirements,
since only the first Taxi to reach the Fare may remove the Fare from the
queue, and all others have to renege out.
        '''

        def __printFareDetails(taxiRange):
            '''DOCSTRING'''
            PRETTY_PRINT = config.getboolean('runtime', 'prettyPrint')
            print
            if PRETTY_PRINT:
                # TODO [lopri] This is unclear.  For example, what does range
                # even mean in this context?  It should refer to the range
                # that a Taxi is looking for Fares.  I am using it to mean the
                # distance (reach) of the Fare's broadcast.  Sort this out.
                #
                # I need to use as many words as it takes
                # to_make_things_clear.  I can shorten them later, but not
                # until things are simpler.
                print '  %.4f Fare %s broadcast stats:' % (now(), fare.name)
                print "    range: %s (based on Fare's time in queue)" % broadcastRange
                print '    time in queue: %.4f' % TIQ
                print '    distance from Taxi: %.4f' % d
                print "    Taxi's range: %.2f (= TAXI_RANGE_XXX * GRID_MAX)" % \
                        (taxiRange*GRID_MAX)
                print '    weight: %.4f (= SIMTIME - TIQ)' % weight
                print '    score: %.4f (= weight + distance)' % score
            else:
                print "  %.4f Fare %s broadcast stats: range: %s, time in queue: %.4f, Taxi's range: %.2f, distance from Taxi: %.4f, weight: %.4f, score: %.4f" \
                        % (now(), fare.name, broadcastRange, TIQ, taxiRange*GRID_MAX, d, weight, score)

        # start of mixedmode_compete()
        tmp=[]
        if not not_a_magic_buffer:
            not_a_magic_buffer = Agent.waitingFares.theBuffer
        VERBOSE = config.getboolean('runtime', 'verbose')
        # I should never hit this, but it can't hurt to leave it in.
        if not len(Agent.waitingFares.theBuffer) > 0:
            print 'Buffer is empty!'
            return
        for fare in not_a_magic_buffer:
            TIQ=(now()-fare.ts['mkreq'])
#            d = getdistance(fare.loc['curr'], self.loc['curr'])
            d=self.map.get_distance(fare.loc['curr'], self.loc['curr'])

            # TODO [eventually] put the weight and scoring routines into a
            # config file.  Major TK.
            weight = SIMTIME - TIQ
            score = weight + d
            f_time_ratio = TIQ/(SIMTIME*1.0)    # force integer division

            # Figure out which category the Fare goes in.  The first part is
            # all about TIME!
            #
            # If Fare has been in the queue long enough for a Global
            # broadcast, calculate score and append to list for further
            # consideration.
            if TAXI_RANGE_MID<f_time_ratio<=TAXI_RANGE_HI:
                broadcastRange = 'GLOBAL'
                if VERBOSE: __printFareDetails(TAXI_RANGE_HI)
                print '.. Pushing (Fare %s, score %.4f) onto list' % (fare.name, score)
                tmp.append((fare, score))

            # Has the Fare been in the queue long enough to be a Regional?
            elif TAXI_RANGE_LOW < f_time_ratio <= TAXI_RANGE_MID:
                broadcastRange = 'REGIONAL'
                if VERBOSE: __printFareDetails(TAXI_RANGE_MID)

                # Is Fare close enough for Taxi to pickup?
                #
                # If distance from Taxi to Fare is less than or equal to
                # (TAXI_RANGE_MID * GRID_MAX), then broadcast is received by
                # Taxi, and Fare gets added to the queue.
                if d <= (TAXI_RANGE_MID * GRID_MAX):
                    print '.. Pushing (Fare %s, score %.4f) onto list' % (fare.name, score)
                    tmp.append((fare, score))
                else:
                    # Fare's been around long enough for it's broadcast to be
                    # Regional, but this Taxi is not in range.  Break out of
                    # the loop, and evaluate the next Fare.
                    if DEBUG:
                        print '  Fare %s:' % fare.name,
                        print 'regional broadcast, but Fare is out of range:',
                        print '(distance) %.1f > (range) %.1f' % (d, TAXI_RANGE_MID * GRID_MAX)
                    if (d < (TAXI_RANGE_LOW * GRID_MAX)):
                        print 'Regional broadcast is broken!'
                    continue

            # It's a local broadcast
            else:
                broadcastRange = 'LOCAL'
                if VERBOSE: __printFareDetails(TAXI_RANGE_LOW)
                # The Fare has only been in the queue long enough to be a Local
                if d <= (TAXI_RANGE_LOW  * GRID_MAX):
                    print '.. Pushing (Fare %s, score %.4f) onto list' % (fare.name, score)
                    tmp.append((fare, score))
                else:
                    # Local broadcast, but this Taxi is not in range.  Break
                    # out of the loop, and evaluate the next Fare.
                    if DEBUG:
                        print '  Fare %s:' % fare.name,
                        print 'local broadcast, but Fare is out of range:',
                        print '(distance) %.1f > (range) %.1f' % (d, TAXI_RANGE_LOW * GRID_MAX)
                    if (d < TAXI_RANGE_LOW * GRID_MAX):
                        print 'Local broadcast is broken!'
                    continue

        # If I hit the continue every time, it's easy to get here and have
        # nothing in tmp2.  I'm not sure this is right, but it seems to me
        # that if there's nothing here, the only thing left to do is return.
        if len(tmp) == 0:
            if DEBUG:
                print '%.4f INFO:' % now(),
                print 'There are no eligible Fares for this Taxi.  Entering getQ...'
            return
        tmp2 = sorted(tmp, key=itemgetter(1))
        result = map(itemgetter(0), tmp2)[0]
        # Borrowed from the bottom of closestfare.  Applies here as well.
        #
        # Critical difference between the competition and cooperative
        # closestfares: cooperative cf returns the Fare, and removes it from
        # waitingFares.theBuffer immediately.  Competition cf just returns the
        # Fare, without taking it out of the buffer until later.  Note also,
        # this method returns a single Fare, not (a list containing) a single
        # Fare.
        return result


#def get_my_fare(fare_list):
##    print "inside get_my_fare"
##    print "returning a list containing Fare", fare.name, "only"
##    print "type([fare]):", type([fare])
##    print "type(fare_list):", type(fare_list)
#    return fare_list


def closestfare_cooperate(buffer):
    '''
Filter: return the Fare that is geographically closest to the calling Taxi.

Implementation detail: I think the name 'buffer' is mentioned in the
documentation as being a special trigger for SimPy, to ensure "magic
behavior".

NOTE: The Fare is returned as a single-element list, because that (a list) is
what SimPy's yield is expecting.  This is a filter function for the Store, and
should not be called directly.  This is the second of the Taxi's three
negotiation protocols.
    '''
    tmp = []
    if not len(Agent.waitingFares.theBuffer) > 0:
        print 'Buffer is empty!'
        return
    if DEBUG:
        if buffer == Agent.waitingFares.theBuffer:
            print 'the buffers are equal'
        if buffer is Agent.waitingFares.theBuffer:
            print 'the buffers are the same'
    for fare in buffer:
        #d = getdistance(fare.loc['curr'], taxi_loc)
        d = Agent.map.get_distance(fare.loc['curr'], taxi_loc)
        if DEBUG:
            print 'Distance from Taxi to Fare %s: %.4f' % (fare.name, d)
        tmp.append((fare, d))
    tmp2 = sorted(tmp, key=itemgetter(1))
    result=[]
    result.append(map(itemgetter(0), tmp2)[0])
    return result


# Third of the Taxi's three negotiation protocols.  See notes in
# ~/finalproject/agents/docs/daily_status/2007/04/08.txt
def mixedmode_cooperate(buffer):
    '''
Filter: find the Fare with the lowest aggregate score.

The Taxi uses a combination of the Fare's time in the waitingFares buffer plus
their distance from the Taxi to determine which Fares to inspect.  If there
are any Fares in this list, then the one with the lowest score (cost) is
returned to the caller.

If the list is empty, in other words, if there are no Fares which meet the
time and space (distance) requirements of this Taxi, the Taxi goes into a
getQ, and stays there until at least one suitable Fare comes along.
Fortunately, there don't seem to be any restrictions from SimPy on when a Taxi
can get out of the queue.  It's just a matter of satisfying the filter
function.

NOTE: The Fare is returned as a single-element list, because that's what
SimPy's yield is expecting.  This is a filter function for the Store, and
should not be called directly.  This is the third of the Taxi's three
negotiation protocols.
    '''
    def __printFareDetails(taxiRange):
        '''DOCSTRING'''
        PRETTY_PRINT = config.getboolean('runtime', 'prettyPrint')
        print
        if PRETTY_PRINT:
            # TODO [lopri] This is unclear.  For example, what does range even
            # mean in this context?  It should refer to the range that a Taxi
            # is looking for Fares.  I am using it to mean the distance
            # (reach) of the Fare's broadcast.  Sort this out.
            #
            # I need to use as many words as it takes to_make_things_clear.  I
            # can shorten them later, but not until things are simpler.
            print '  %.4f Fare %s broadcast stats:' % (now(), fare.name)
            print "    range: %s (based on Fare's time in queue)" % broadcastRange
            print '    time in queue: %.4f' % TIQ
            print '    distance from Taxi: %.4f' % d
            print "    Taxi's range: %.2f (= TAXI_RANGE_XXX * GRID_MAX)" % (taxiRange*GRID_MAX)
            print '    weight: %.4f (= SIMTIME - TIQ)' % weight
            print '    score: %.4f (= weight + distance)' % score
        else:
            print "  %.4f Fare %s broadcast stats: range: %s, time in queue: %.4f, Taxi's range: %.2f, distance from Taxi: %.4f, weight: %.4f, score: %.4f" \
                    % (now(), fare.name, broadcastRange, TIQ, taxiRange*GRID_MAX, d, weight, score)

    # start of mixedmode_cooperate()
    tmp = []
    VERBOSE = config.getboolean('runtime', 'verbose')
    # I should never hit this, but it can't hurt to leave it in.
    if not len(Agent.waitingFares.theBuffer) > 0:
        print 'Buffer is empty!'
        return
    for fare in buffer:
        TIQ=(now()-fare.ts['mkreq'])
        #d = getdistance(fare.loc['curr'], taxi_loc)
        d=Agent.map.get_distance(fare.loc['curr'], taxi_loc)
        # TODO [eventually] put the weight and scoring routines into a config
        # file.  Major TK.
        weight = SIMTIME - TIQ
        score = weight + d
        f_time_ratio = TIQ/(SIMTIME*1.0)    # force integer division

        # Figure out which category the Fare goes in.  The first part is all
        # about TIME!
        #
        # If Fare has been in the queue long enough for a Global broadcast,
        # calculate score and append to list for further consideration.
        if TAXI_RANGE_MID < f_time_ratio <= TAXI_RANGE_HI:
            broadcastRange = 'GLOBAL'
            if VERBOSE: __printFareDetails(TAXI_RANGE_HI)
            print '.. Pushing (Fare %s, score %.4f) onto list' % (fare.name, score)
            tmp.append((fare, score))

        # Has the Fare been in the queue long enough to be a Regional?
        elif TAXI_RANGE_LOW < f_time_ratio <= TAXI_RANGE_MID:
            broadcastRange = 'REGIONAL'
            if VERBOSE: __printFareDetails(TAXI_RANGE_MID)

            # Is Fare close enough for Taxi to pickup?
            #
            # If distance from Taxi to Fare is less than or equal to
            # (TAXI_RANGE_MID * GRID_MAX), then broadcast is received by Taxi,
            # and Fare gets added to the queue.
            if d <= (TAXI_RANGE_MID * GRID_MAX):
                print '.. Pushing (Fare %s, score %.4f) onto list' % (fare.name, score)
                tmp.append((fare, score))
            else:
                # Fare's been around long enough for it's broadcast to be
                # Regional, but this Taxi is not in range.  Break out of the
                # loop, and evaluate the next Fare.
                if DEBUG:
                    print '  Fare %s:' % fare.name,
                    print 'regional broadcast, but Fare is out of range:',
                    print '(distance) %.1f > (range) %.1f' % (d, TAXI_RANGE_MID * GRID_MAX)
                if (d < (TAXI_RANGE_LOW * GRID_MAX)):
                    print 'Regional broadcast is broken!'
                continue

        # It's a local broadcast
        else:
            broadcastRange = 'LOCAL'
            if VERBOSE: __printFareDetails(TAXI_RANGE_LOW)
            # The Fare has only been in the queue long enough to be a Local
            if d <= (TAXI_RANGE_LOW  * GRID_MAX):
                print '.. Pushing (Fare %s, score %.4f) onto list' % (fare.name, score)
                tmp.append((fare, score))
            else:
                # Local broadcast, but this Taxi is not in range.  Break out
                # of the loop, and evaluate the next Fare.
                if DEBUG:
                    print '  Fare %s:' % fare.name,
                    print 'local broadcast, but Fare is out of range:',
                    print '(distance) %.1f > (range) %.1f' % (d, TAXI_RANGE_LOW * GRID_MAX)
                if (d < TAXI_RANGE_LOW * GRID_MAX):
                    print 'Local broadcast is broken!'
                continue

    # If I hit the continue every time, it's easy to get here and have nothing
    # in tmp2.  I'm not sure this is right, but it seems to me that if there's
    # nothing here, the only thing left to do is return.
    if len(tmp) == 0:
        if DEBUG:
            print '%.4f INFO:' % now(),
            print 'There are no eligible Fares for this Taxi.  Entering getQ...'
        return
    tmp2 = sorted(tmp, key=itemgetter(1))
    result = map(itemgetter(0), tmp2)[0]
    return [result]


###def getdistance(dest, currentLocation=None):
###    '''
###Given a pair of coordinates, return the distance between them (float).
###
###Returns the straight-line distance between the points ("as the crow
###flies") if hypotenuse is True, (the default).  Otherwise, returns the
###driving distance.
###    '''
###    # CAUTION: the compete methods do not use taxi_loc, so this is a hazard.
###    curr = currentLocation #or taxi_loc
###    if not curr:
###        print 'What am I supposed to do with an empty current location tuple??'
###        print 'dest:', dest, 'curr:', curr
###        stopSimulation()
####        import sys; sys.exit()
###
###        # NO more stubby!!  Woo!
###        #
###        # Here's cooperate/FIFO, closestfare, mixedmode
###        #
###        #[timc@apostrophe agents/package] python driver.py | grep stubby | wc
###        #       0       0       0
###        #[timc@apostrophe agents/package] python driver.py | grep stubby | wc
###        #       0       0       0
###        #[timc@apostrophe agents/package] python driver.py | grep stubby | wc
###        #       0       0       0
###        #
###        # Here's compete/FIFO, closestfare, mixedmode
###        #
###        #[timc@apostrophe agents/package] python driver.py | grep stubby | wc
###        #       0       0       0
###        #[timc@apostrophe agents/package] python driver.py | grep stubby | wc
###        #       0       0       0
###        #[timc@apostrophe agents/package] python driver.py | grep stubby | wc
###        #       0       0       0
###
###        print 'more stubby!'
####        a = Agent('stubby')
####        curr = a.mkcoords()
###
###    DC = config.get('runtime', 'distanceCalculation')
###    if DC == 'straightLine':    # use the hypotenuse
###        return math.hypot((curr[0]-dest[0]), (curr[1]-dest[1]))
###    elif DC == 'drivingDistance':
###        return abs(curr[0]-dest[0]) + abs(curr[1]-dest[1])


if __name__ == '__main__':
    t = Taxi('Terence', 'FIFO')
    t.cooperate()
    print "updating negotiation protocol to 'closestfare' and running again"
    t.np = 'closestfare'
    t.cooperate()
    # TODO [eventually] add in the rest of the simulation runs
