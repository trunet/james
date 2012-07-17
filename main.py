# -*- coding: utf-8 -*-

"""
Copyright (C) 2011 Wagner Sartori Junior <wsartori@gmail.com>
http://www.wsartori.com

This file is part of James.

James program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""

from twisted.python import log, usage
from twisted.internet import reactor, task
from twisted.internet.serialport import SerialPort
from twisted.web.client import getPage

from txosc import osc
from txosc import dispatch
from txosc import async

import txpachube
import txpachube.client

import simplejson as json
import rfc822
import ntplib
import datetime
import time

import re
from struct import unpack, pack

import sys

from txXBee.protocol import txXBee

feed_data = """<?xml version="1.0" encoding="UTF-8"?>
<eeml xmlns="http://www.eeml.org/xsd/0.5.1" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" version="0.5.1" xsi:schemaLocation="http://www.eeml.org/xsd/0.5.1 http://www.eeml.org/xsd/0.5.1/0.5.1.xsd">
  <environment>
    <title>Weather Station, Morumbi Sao Paulo/SP</title>
    <status>live</status>
    <description>A Weather Station on Morumbi, Sao Paulo/SP</description>
    <tag>brasil</tag>
    <tag>brazil</tag>
    <tag>humidity</tag>
    <tag>morumbi</tag>
    <tag>pressure</tag>
    <tag>temperature</tag>
    <tag>weather</tag>
    <tag>station</tag>
    <tag>wind</tag>
    <tag></tag>
    <tag></tag>
    <data id="battery_voltage">
      <current_value>%.2f</current_value>
    </data>
    <data id="humidity">
      <current_value>%.2f</current_value>
    </data>
    <data id="pressure">
      <current_value>%.2f</current_value>
    </data>
    <data id="solar_voltage">
      <current_value>%.2f</current_value>
    </data>
    <data id="temperature">
      <current_value>%.2f</current_value>
    </data>
  </environment>
</eeml>"""

def strip_accents(string):
	import unicodedata
	return unicodedata.normalize('NFKD', unicode(string, encoding="utf-8")).encode('ASCII', 'ignore')

def printData(d):
	print d

def printError(failure):
	#import sys
	#sys.stderr.write(str(failure))
	print str(failure)

class JamesOptions(usage.Options):
	optParameters = [
		['outfile', 'o', None, 'Logfile [default: sys.stdout]'],
		['baudrate', 'b', 38400, 'Serial baudrate [default: 38400'],
		['port', 'p', '/dev/tty.usbserial-A600ezgH', 'Serial Port device'],
	]

devices = {
	"pc": "\x00\x13\xA2\x00\x40\x66\x5D\xB3",
	"energymonitor": "\x00\x13\xA2\x00\x40\x5D\x35\x04",
	"trunetclock": "\x00\x13\xA2\x00\x40\x66\x5D\xEF",
	"ledcube": "\x00\x13\xA2\x00\x40\x6F\xB7\x68",
	"weatherstation": "\x00\x13\xA2\x00\x40\x6F\xBA\xE5",
	"template": "\x00\x13\xA2\x00\x00\x00\x00\x00",
}

PACHUBE_API_KEY = "SZOaog34iYDlHOx1NjSbIE3tXxpuibIRtFUW8vLUtWw"
FEED_ID = "67925"

class James(txXBee):
	def __init__(self, *args, **kwds):
		super(James, self).__init__(*args, **kwds)
		self.msgNumber = 0

		self.lc_getTemp = task.LoopingCall(self.getTempNrpe)
		self.lc_getTemp.start(30.0)

		self.lc_watt = task.LoopingCall(self.getWatts)
		self.lc_watt.start(35.0)

		self.lc_getWeatherStation = task.LoopingCall(self.getWeatherStation)
		self.lc_getWeatherStation.start(60.0)

		#self.lc_get_sigasantos = task.LoopingCall(self.getSantos)
		#self.lc_get_sigasantos.start(300.0)
		
		self.lc_printOnClock = task.LoopingCall(self.printOnClock)
		self.lc_printOnClock.start(35.0)

		#TXOSC
		self.port = 8000
		self.dest_port = 9000
		self.receiver = dispatch.Receiver()
		self.sender = async.DatagramClientProtocol()
		self._sender_port = reactor.listenUDP(0, self.sender)
		self._server_port = reactor.listenUDP(self.port, async.DatagramServerProtocol(self.receiver))
		self.receiver.addCallback("/trunetclock/brightness", self.trunetclock_brightness_handler)
		self.receiver.addCallback("/ledcube/effect", self.ledcube_effect_handler)
		self.receiver.addCallback("/ledcube/turnoff", self.ledcube_turnoff_handler)
		#self.sender.send(osc.Message("/radio/tuner", float(line)/10), ("192.168.142.145", self.dest_port))

		self.pachube = txpachube.client.Client(api_key=PACHUBE_API_KEY, feed_id=FEED_ID)
	
	def trunetclock_brightness_handler(self, message, address):
		brightness = int(message.getValues()[0])
		reactor.callFromThread(self.send,
		          "tx",
		          frame_id="\x01",
		          dest_addr_long=devices["trunetclock"],
		          dest_addr="\xff\xfe",
		          data="\x54" +
		               chr(brightness))

	#Effects Builtin List:
	#effect = 1 # RAIN
	#effect = 2 # Random Z
	#effect = 3 # Random Filler
	#effect = 4 # Up Down Z
	#effect = 5 # Worm Squeeze 2 width
	#effect = 6 # Blinky2
	#effect = 7 # Boz Shrink Grow
	#effect = 8 # Plan Boing
	#effect = 9 # Stairs
	#effect = 10 # Random Suspend X, Y and Z
	#effect = 11 # Load Bar
	#effect = 12 # Worm Squeeze 1 width
	#effect = 13 # String Fly "Trunet LedCube"
	#effect = 14 # Game Of Life
	#effect = 15 # Up Down, Left Right, Front Back
	#effect = 16 # Worm running longer than 27
	#effect = 17 # Smiley Spin Up
	#effect = 18 # Spiral
	#effect = 19 # Arrow running around
	#effect = 20 # Smiley Spin Down
	#effect = 21 # Path "TRUNET" running around
	#effect = 22 # Random Path Around
	#effect = 23 # Worm Squeeze 1 width (-1)
	#effect = 24 # Arrow Spinning
	#effect = 25 # Random Pixels on Cube <== COOL
	#effect = 26 # Worm Squeeze 1 width (same as 23)
	#effect = 27 # Worm running smaller than 16	
	def ledcube_effect_handler(self, message, address):
		effect = int(message.getValues()[0])
		if effect > 0:
			log.msg("Doing effect number " + str(effect))
			reactor.callFromThread(self.send,
			          "tx",
			          frame_id="\x01",
			          dest_addr_long=devices["ledcube"],
			          dest_addr="\xff\xfe",
			          data="\xff" + chr(effect))
		
	def ledcube_turnoff_handler(self, message, address):
		log.msg("Turning off")
		reactor.callFromThread(self.send,
		          "tx",
		          frame_id="\x01",
		          dest_addr_long=devices["ledcube"],
		          dest_addr="\xff\xfe",
		          data="\xff\xfe")
	
	def decodeFloat(self, var):
		text = ""
		for i in range(0, len(var)):
			text += var[i]
		return unpack('f', text)[0]
		
	def encodeFloat(self, var):
		var = unpack('<L', pack('<f', var))[0]
		v1 = var & 0xff
		v2 = (var >> 8) & 0xff
		v3 = (var >> 16) & 0xff
		v4 = (var >> 24) & 0xff
		return chr(v1) + chr(v2) + chr(v3) + chr(v4)

	def handle_packet(self, xbeePacketDictionary):
		response = xbeePacketDictionary
		if (response.get("source_addr_long", "default") != "default"):
			log.msg("Received packet from: " + response.get("source_addr_long").encode("hex"))
		if (response.get("rf_data", "default") != "default"):
			if len(response.get("rf_data")) > 0:
				if response.get("rf_data")[0] == "\x01":
					c = ntplib.NTPClient()
					try:
						response = c.request('br.pool.ntp.org', version=3)
						timestamp = int(response.tx_time)
					except:
						timestamp = int(time.time())
					finally:
						t1 = timestamp & 0xff
						t2 = (timestamp >> 8) & 0xff
						t3 = (timestamp >> 16) & 0xff
						t4 = (timestamp >> 24) & 0xff
						reactor.callFromThread(self.send,
						          "tx",
						          frame_id="\x01",
						          dest_addr_long=response.get("source_addr_long"),
						          dest_addr="\xff\xfe",
						          data=chr(t1) + chr(t2) + chr(t3) + chr(t4))
		if response.get("source_addr_long", "default").lower() == devices["energymonitor"].lower():
			log.msg("Received energy monitor packet, sending to clock...")
			reactor.callFromThread(self.send,
			          "tx",
			          frame_id="\x01",
			          dest_addr_long=devices["trunetclock"],
			          dest_addr="\xff\xfe",
			          data="\x51" +
			               "\x00" + 
			               "Consumo: " + str(int(self.decodeFloat(response["rf_data"][0:4]))) +
			               " Watts")
		elif response.get("source_addr_long", "default").lower() == devices["weatherstation"].lower():
			self.handle_weather_station_packet(response["rf_data"])

	def handle_weather_station_packet(self, data):
		def send_to_clock(data):
			log.msg("Received weather station packet, sending to clock...")
			self.send(
			          "tx",
			          frame_id="\x01",
			          dest_addr_long=devices["trunetclock"],
			          dest_addr="\xff\xfe",
			          data="\x51" +
			               "\x01" + 
			               "WS - T: %.1fC, P: %.1fhPa, H: %.1f%%" % (self.decodeFloat(data[12:16]), self.decodeFloat(data[0:4]), self.decodeFloat(data[8:12]))
			)
		
		def send_to_pachube(data):
			log.msg("Sending to pachube... ")
			self.pachube.update_feed(format=txpachube.DataFormats.XML, data=feed_data % (self.decodeFloat(data[18:22]), self.decodeFloat(data[8:12]), self.decodeFloat(data[0:4]), self.decodeFloat(data[22:26]), self.decodeFloat(data[12:16])))

		reactor.callFromThread(send_to_clock, data)
		reactor.callFromThread(send_to_pachube, data)

	def printOnClock(self):
		if self.msgNumber == 2: #mude para 6 para todas mensagens
			self.msgNumber = 0

		scrollingPacket = ord("\xe0") | (self.msgNumber << 1)
		
		log.msg("Scrolling on clock message " + str(self.msgNumber))
		
		reactor.callFromThread(self.send,
		         "tx",
		         frame_id="\x01",
		         dest_addr_long=devices["trunetclock"],
		         dest_addr="\xff\xfe",
		         data="\x52" + chr(scrollingPacket))
		
		self.msgNumber += 1

	def getWatts(self):
		log.msg("Asking power consumption...")
		reactor.callFromThread(self.send,
		             "tx",
		             frame_id="\x01",
		             dest_addr_long=devices["energymonitor"],
		             dest_addr="\xff\xfe",
		             data="\x05")

	def getWeatherStation(self):
		log.msg("Getting weather station packet...")
		reactor.callFromThread(self.send,
		             "tx",
		             frame_id="\x01",
		             dest_addr_long=devices["weatherstation"],
		             dest_addr="\xff\xfe",
		             data="\x01")

	def getTempNrpe(self):
		log.msg("Getting temperature from nrpe server...")
		import subprocess
		import re
		try:
			#output = subprocess.check_output(["./check_nrpe", "-H", "192.168.142.1", "-c", "check_temperature"])
			output = subprocess.check_output(["/usr/lib/nagios/plugins/check_temp_watchport.py", "--temperature", "--wmin", "10", "--wmax",  "35", "--cmin", "5", "--cmax", "40"])
		except:
			reactor.callFromThread(self.send,
		            "tx",
		            frame_id="\x01",
		            dest_addr_long=devices["trunetclock"],
		            dest_addr="\xff\xfe",
		            data="\x56" + self.encodeFloat(float(0)))
		finally:
			try:
				m = re.search('^.*\s-\s.*\|\w*=(.*);;;;$', output)
				log.msg("Sending temperature: " + m.group(1))
				reactor.callFromThread(self.send,
				            "tx",
				            frame_id="\x01",
				            dest_addr_long=devices["trunetclock"],
				            dest_addr="\xff\xfe",
				            data="\x56" + self.encodeFloat(float(m.group(1))))
			except:
				reactor.callFromThread(self.send,
		            "tx",
		            frame_id="\x01",
		            dest_addr_long=devices["trunetclock"],
		            dest_addr="\xff\xfe",
		            data="\x56" + self.encodeFloat(float(0)))
		
	def getSantos(self):
		def parseFeed(data):
			return json.loads(data)
			
		def saveData(feed):
			santos = []
			for item in range(0, len(feed)):
				title = strip_accents(feed[item]['text'].encode('utf-8'))
				title = title.replace("\n", " ")
				
				# Regular Expression to get what I want
				reg_notice = re.compile('^(.*)(: http://.*)$').search(title)
				reg_jogo = re.compile('^(.*)( Siga AO VIVO por aqui!)$').search(title)
				if reg_jogo is not None:
					title = reg_jogo.groups()[0][:90]
				elif reg_notice is not None:
					title = reg_notice.groups()[0][:90]
				else:
					title = title[:90]
				
				reg_via = re.compile('^(.*) - Via .*$').search(title)
				if reg_via is not None:
					title = reg_via.groups()[0][:90]
				
				created_at = rfc822.parsedate(feed[item]['created_at'])
				if (time.mktime(created_at) >= (time.time() - (60*60*12))):
					santos.append({
				 					'created_at': created_at,
				 					'title': title
						  	   	  })
			return santos
		
		def sendToClock(data):
			msg = 0
			for item in range(0, len(data)):
				item = data.pop(0)
				if msg == 6:
					break
				title = item['title']
				log.msg("Sending to Clock on Address " + str(msg) + " - " + title)
				reactor.callFromThread(self.send,
				         "tx",
				         frame_id="\x01",
				         dest_addr_long=devices["trunetclock"],
				         dest_addr="\xff\xfe",
				         data="\x51" +
				              chr(msg) + 
				              title)
				msg += 1
		
		def logError(error):
			log.msg("Error downloading SigaSantos RSS from twitter" + str(error))
		
		page = getPage("http://api.twitter.com/1/statuses/user_timeline.json?screen_name=sigasantos")
		
		page.addCallback(parseFeed)
		page.addErrback(logError)
		
		page.addCallback(saveData)
		page.addErrback(logError)
		
		page.addCallback(sendToClock)
		page.addErrback(logError)

if __name__ == '__main__':
	o = JamesOptions()
	try:
		o.parseOptions()
	except usage.UsageError, errortext:
		print '%s: %s' % (sys.argv[0], errortext)
		print '%s: Try --help for usage details.' % (sys.argv[0])
		raise SystemExit, 1

	logFile = o.opts['outfile']
	if logFile is None:
		logFile = sys.stdout
	log.startLogging(logFile)

	port = o.opts['port']
	log.msg('Attempting to open %s at %dbps as a %s device' % (port, o.opts['baudrate'], txXBee.__name__))
	
	s = SerialPort(James(), o.opts['port'], reactor, baudrate=o.opts['baudrate'])
	
	reactor.run()
