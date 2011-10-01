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

import simplejson as json
import rfc822
import ntplib
import datetime
import time

import re
from struct import unpack

import sys

from xbeeService.protocol import ZigBeeProtocol

def strip_accents(string):
	import unicodedata
	return unicodedata.normalize('NFKD', unicode(string, encoding="utf-8")).encode('ASCII', 'ignore')

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
	"template": "\x00\x13\xA2\x00\x00\x00\x00\x00",
}

class James(ZigBeeProtocol):
	def __init__(self, *args, **kwds):
		super(James, self).__init__(*args, **kwds)
		self.msgNumber = 1

		self.lc_watt = task.LoopingCall(self.getWatts)
		self.lc_watt.start(35.0)
		
		self.lc_get_sigasantos = task.LoopingCall(self.getSantos)
		self.lc_get_sigasantos.start(60.0)
		
		self.lc_printOnClock = task.LoopingCall(self.printOnClock)
		self.lc_printOnClock.start(35.0, now=False)
		
		#TXOSC
		self.port = 8000
		self.dest_port = 9000
		self.receiver = dispatch.Receiver()
		self.sender = async.DatagramClientProtocol()
		self._sender_port = reactor.listenUDP(0, self.sender)
		self._server_port = reactor.listenUDP(self.port, async.DatagramServerProtocol(self.receiver))
		self.receiver.addCallback("/trunetclock/brightness", self.trunetclock_brightness_handler)
		#self.sender.send(osc.Message("/radio/tuner", float(line)/10), ("192.168.142.145", self.dest_port))
	
	def trunetclock_brightness_handler(self, message, address):
		brightness = int(message.getValues()[0])
		reactor.callFromThread(self.send,
		          "tx",
		          frame_id="\x01",
		          dest_addr_long=devices["trunetclock"],
		          dest_addr="\xff\xfe",
		          data="\x54" +
		               chr(brightness))
	
	def decodeFloat(self, var):
		text = ""
		for i in range(0, len(var)):
			text += var[i]
		return unpack('f', text)[0]

	def handle_packet(self, xbeePacketDictionary):
		response = xbeePacketDictionary
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
		elif response.get("source_addr_long", "default") == devices["energymonitor"]:
			reactor.callFromThread(self.send,
			          "tx",
			          frame_id="\x01",
			          dest_addr_long=devices["trunetclock"],
			          dest_addr="\xff\xfe",
			          data="\x51" +
			               "\x00" + 
			               "Consumo: " + str(int(self.decodeFloat(response["rf_data"][0:4]))) +
			               " Watts")

	def printOnClock(self):
		if self.msgNumber == 8:
			self.msgNumber = 1

		scrollingPacket = ord("\xe0") | (self.msgNumber << 1)
		self.msgNumber += 1
		
		log.msg("Scrolling on clock message " + str(self.msgNumber-1))
		
		reactor.callFromThread(self.send,
		         "tx",
		         frame_id="\x01",
		         dest_addr_long=devices["trunetclock"],
		         dest_addr="\xff\xfe",
		         data="\x52" + chr(scrollingPacket))

	def getWatts(self):
		log.msg("Asking power consumption...")
		reactor.callFromThread(self.send,
		             "tx",
		             frame_id="\x01",
		             dest_addr_long=devices["energymonitor"],
		             dest_addr="\xff\xfe",
		             data="\x05")

	def getSantos(self):
		def parseFeed(data):
			return json.loads(data)
			
		def saveData(feed):
			santos = []
			for item in range(0, len(feed)):
				title = strip_accents(feed[item]['text'].encode('utf-8'))
				
				# Regular Expression to get what I want
				reg_notice = re.compile('^(.*)(: http://.*)$').search(title)
				reg_jogo = re.compile('^(.*)( Siga AO VIVO por aqui!)$').search(title)
				if reg_jogo is not None:
					title = reg_jogo.groups()[0][:90]
				elif reg_notice is not None:
					title = reg_notice.groups()[0][:90]
				else:
					title = title[:90]
				
				created_at = rfc822.parsedate(feed[item]['created_at'])
				if (time.mktime(created_at) >= (time.time() - (60*60*12))):
					santos.append({
				 					'created_at': created_at,
				 					'title': title
						  	   	  })
			return santos
		
		def sendToClock(data):
			msg = 1
			for item in range(0, len(data)):
				item = data.pop(0)
				if msg == 8:
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
	log.msg('Attempting to open %s at %dbps as a %s device' % (port, o.opts['baudrate'], ZigBeeProtocol.__name__))
	
	s = SerialPort(James(), o.opts['port'], reactor, baudrate=o.opts['baudrate'])
	
	reactor.run()
