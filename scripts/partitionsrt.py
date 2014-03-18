#!/usr/bin/python

import sys
import re
import sqlite3
import os
import commands
from optparse import OptionParser

BLOCK_GAP = 1.0
BLOCK_MARGIN = 0.5

def timestamp_to_seconds(timestamp):
	hours, minutes, seconds = timestamp.strip().split(':')
	return 3600 * int(hours) + 60 * int(minutes) + float(seconds)

def seconds_to_timestamp(seconds):
	hours = int(seconds / 3600)
	minutes = int(seconds / 60)
	seconds = seconds % 60
	return '{hours}:{minutes}:{seconds}'.format(
		hours=hours,
		minutes=minutes,
		seconds=seconds)

class SRTBreak(object):
	pass

class SRTUnit(object):
	def __init__(self, unit_id, timestamp_begin, timestamp_end, text):
		self.unit_id = unit_id
		self.timestamp_begin = timestamp_begin
		self.timestamp_end = timestamp_end
		self.text = ' '.join(text.splitlines())

	def __str__(self):
		return "{begin} - {end}: {text}".format(
			begin=self.timestamp_begin,
			end=self.timestamp_end,
			text=self.text)

	def __repr__(self):
		return str(self)

class SRTBlock(object):
	def __init__(self):
		self.timestamp_begin = None
		self.timestamp_end = None
		self.text_lines = []

	def text(self):
		return '\n'.join(self.text_lines)

	def add(self, unit):
		if isinstance(unit, SRTBreak):
			return
		if self.timestamp_begin is None:
			self.timestamp_begin = unit.timestamp_begin
		self.timestamp_end = unit.timestamp_end
		new_text = re.sub(r'\(.*\)', '', unit.text)
		new_text = re.sub(r'\[.*\]', '', new_text).strip()
		if new_text:
			self.text_lines.append(new_text)

	def should_add(self, unit):
		if isinstance(unit, SRTBreak):
			return False
		elif self.timestamp_end is None and self.timestamp_begin is None:
			return True
		else:
			end = timestamp_to_seconds(self.timestamp_end)
			new_begin = timestamp_to_seconds(unit.timestamp_begin)
			return new_begin - end < BLOCK_GAP

	def title(self):
		t = self.text().lower()
		t = re.sub(r'[^a-z \n]', '', t)
		return '-'.join(t.split()[:6])

	def __str__(self):
		return "{begin} - {end}: {text}".format(
			begin=self.timestamp_begin,
			end=self.timestamp_end,
			text=self.text())

	def __repr__(self):
		return str(self)

def splitsrt(fname):
	raw = open(fname).read()
	units = raw.strip().split('\n\n')
	ret = []
	for unit in units:
		unit = unit.strip()
		if not unit:
			continue
		elif unit.strip() == 'BREAK':
			ret.append(SRTBreak())
		else:
			try:
				lines = unit.splitlines()
				unit_id = lines[0]
				timestamp_begin, timestamp_end = lines[1].replace(',', '.').split(' --> ')
				text = '\n'.join(lines[2:])
				ret.append(SRTUnit(unit_id, timestamp_begin, timestamp_end, text))
			except:
				print >> sys.stderr, "Problem handling unit:"
				print >> sys.stderr, unit
	return ret

def combinesrt(units):
	ret = []
	current_block = SRTBlock()
	for unit in units:
		if not current_block.should_add(unit):
			ret.append(current_block)
			current_block = SRTBlock()
		current_block.add(unit)
	ret.append(current_block)
	ret = [block for block in ret if block.text()]
	return ret

def preprocess(vid_fname):
	track_info = commands.getoutput('mkvmerge -i {vid_fname}'.format(vid_fname=vid_fname))
	subtitle_field = None
	for line in track_info.splitlines():
		if line.startswith('Track ID ') and 'subtitles' in line:
			subtitle_field = int(line.split()[2].rstrip(':'))
			break
	vid_prefix = vid_fname[:-len('.mkv')]
	assert subtitle_field != None, "Could not find subtitle track."

	extract_cmd = "mkvextract tracks {vid_fname} {subtitle_field}:{vid_prefix}.sub".format(
		subtitle_field=subtitle_field,
		vid_fname=vid_fname,
		vid_prefix=vid_prefix)
	assert os.system(extract_cmd) == 0, "Unable to extract subtitles field."

	convert_cmd = r"""vobsub2srt --blacklist "|\\/\`_~<>" """ + vid_prefix
	assert os.system(convert_cmd) == 0, "Unable to convert VobSub to SRT file."

optparser = OptionParser()
optparser.add_option('-d', '--db',  help="Filename of the quotes.db output.", dest="db_fname")
optparser.add_option('-v', '--vid', help="Filename of the video file.", dest="vid_fname")
optparser.add_option('-s', '--srt', help="Filename of the .srt file.", dest="srt_fname")
optparser.add_option('-o', '--out', help="Directory to place output files.", dest="output_dname")

if __name__ == '__main__':
	(options, args) = optparser.parse_args()

	command = args[0]
	assert command in ('info', 'process', 'preprocess'), "Invalid command, must select 'info' or 'process' as first argument."
	if command == "info":
		assert options.srt_fname, "Must specify .srt filename to read."
	elif command == 'process':
		assert options.srt_fname, "Must specify .srt file in order to process index."
		assert options.vid_fname, "Must specify video file in order to process index."
		assert options.db_fname, "Must specify database output file in order to process index."
		assert options.output_dname, "Must specify output directory in order to process index."
	elif command == 'preprocess':
		assert options.vid_fname, "Must specify video file in order to preprocess."

	if command == 'preprocess':
		preprocess(options.vid_fname)
		sys.exit(0)

	units = splitsrt(options.srt_fname)
	blocks = combinesrt(units)
	totalwords = 0

	if command == 'process':
		dbconn = sqlite3.connect(options.db_fname)
		dbconn.text_factory = str
		cursor = dbconn.cursor()

	for block in blocks:

		if command == 'info':
			print block.title()
			print block
			print
			totalwords += len(block.text().split())

		if command == 'process':
			start = timestamp_to_seconds(block.timestamp_begin) - BLOCK_MARGIN
			diff = timestamp_to_seconds(block.timestamp_end) - timestamp_to_seconds(block.timestamp_begin) + BLOCK_MARGIN * 2
			build_cmd = "ffmpeg -y -i {fname} -ss {start} -t {diff} -sn -vcodec libx264 -acodec libmp3lame {outfile}".format(
				fname=options.vid_fname,
				start=seconds_to_timestamp(start),
				diff=diff,
				outfile=os.path.join(options.output_dname, block.title() + '.mkv')
				)
			screencap_cmd = "ffmpeg -y -i {clip_fname} -ss 00:00:01 -vframes 1 {outfile}".format(
				clip_fname=os.path.join(options.output_dname, block.title() + '.mkv'),
				outfile=os.path.join(options.output_dname, block.title() + '.mkv.jpg'))

			assert os.system(build_cmd) == 0, "Build command failed: %s" % build_cmd
			assert os.system(screencap_cmd) == 0, "Screencap command failed: %s" % screencap_cmd

			# Insert quote information into SQLLite DB.
			cursor.execute("INSERT INTO quotes (title, video_url, preview_url, quote) VALUES (?, ?, ?, ?)",
				(block.title(), block.title() + '.mkv', block.title() + '.mkv.jpg', block.text())
				)

	if command == 'info':
		average = float(totalwords) / len(blocks)
		print 'Number of blocks: %d' % len(blocks)
		print 'Average block length: %d words' % average

	if command == 'process':
		dbconn.commit()
		dbconn.close()