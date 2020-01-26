"""Mozilla IndexedDB object database tools for Python."""
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# Credits:
#   – Source was havily inspired by
#     https://dxr.mozilla.org/mozilla-central/rev/3bc0d683a41cb63c83cb115d1b6a85d50013d59e/dom/indexedDB/Key.cpp
#   – Python source code by Alexander Schlarb, 2020.

import datetime
import enum
import math
import io
import os
import struct
import sqlite3
import time
import typing

import mozserial
import snappy


class KeyType(enum.IntEnum):
	TERMINATOR = 0
	FLOAT      = 0x10
	DATE       = 0x20
	STRING     = 0x30
	BINARY     = 0x40
	ARRAY      = 0x50

class KeyCodec:
	ONE_BYTE_LIMIT = 0x7E
	TWO_BYTE_LIMIT = 0x3FFF + 0x7F
	
	ONE_BYTE_ADJUST = 1
	TWO_BYTE_ADJUST = -0x7F
	THREE_BYTE_SHIFT = 6
	
	@classmethod
	def encode(cls, value: object) -> bytes:
		return cls._encode(value, set())
	
	@classmethod
	def _encode(cls, value: object, seen: typing.Set[int], type_off: int = 0) -> bytes:
		if id(value) in seen:
			raise ValueError("Cannot encode recursive datastructures")
		seen.add(id(value))
		
		if isinstance(value, (int, float)):
			if math.isnan(value):
				raise ValueError("Cannot encode NaN")
			
			return cls.encode_number(value, type_off)
		
		if isinstance(value, str):
			return cls.encode_string(value, type_off)
		
		if isinstance(value, time.struct_time):
			timestamp = time.mktime(value)
			timezone  = datetime.timezone(datetime.timedelta(seconds=value.tm_gmtoff))
			value = datetime.datetime.fromtimestamp(timestamp, timezone)
		
		if isinstance(value, datetime.datetime):
			value = value.astimezone(datetime.timezone.utc).timestamp()
			return cls._encode_number(value, int(KeyType.DATE) + type_off)
	
	@classmethod
	def encode_number(cls, value: typing.Union[int, float], type_off: int = 0) -> bytes:
		buf = bytearray()
		cls._encode_number(buf, float(value), int(KeyType.FLOAT) + type_off)
		return bytes(buf)
	
	def _encode_number(cls, buf: bytearray, value: float, type: int) -> bytes:
		# Write type marker
		buf.append(type)
		
		as_int = struct.unpack("=q", struct.pack("=d", value))[0]
		if value < 0:
			as_int = (0 - as_int) & 0xFFFFFFFFFFFFFFFF
		else:
			as_int |= 0x7000000000000000
		
		buf.append(struct.pack(">q", as_int))
	
	@classmethod
	def encode_binary(cls, value: bytes, type_off: int = 0) -> bytes:
		buf = bytearray()
		cls._encode_string(buf, value.decode("latin-1"), int(KeyType.BINARY) + type_off)
		return bytes(buf)
	
	@classmethod
	def encode_string(cls, value: str, type_off: int = 0) -> bytes:
		buf = bytearray()
		cls._encode_string(buf, value, int(KeyType.STRING) + type_off)
		return bytes(buf)
	
	@classmethod
	def _encode_string(cls, buf: bytearray, value: str, type: int) -> bytes:
		# Write type marker
		buf.append(type)
		
		# Encode string
		for uscalar in map(ord, value):
			# Strings are encoded per UTF-16 codepoint
			if uscalar <= 0xFFFF:
				codepoints = (uscalar,)
			else:
				codepoints = ((uscalar >> 10) | 0xD800, (uscalar & 0x3FF) | 0xDC00)
			
			for c in codepoints:
				if c <= cls.ONE_BYTE_LIMIT:
					buf.append(c + cls.ONE_BYTE_ADJUST)
				elif c <= cls.TWO_BYTE_LIMIT:
					c += cls.TWO_BYTE_ADJUST + 0x8000
					buf.append((c >> 8) & 0xFF)
					buf.append((c >> 0) & 0xFF)
				else:
					c = (c << cls.THREE_BYTE_SHIFT) | 0x00C00000
					buf.append((c >> 16) & 0xFF)
					buf.append((c >> 8)  & 0xFF)
					buf.append((c >> 0)  & 0xFF)
		
		#buf.append(int(KeyType.TERMINATOR))


class IndexedDB(sqlite3.Connection):
	def __init__(self, dbpath: typing.Union[os.PathLike, str, bytes]):
		super().__init__(dbpath)
	
	def read_object(self, key_name: str):
		key = KeyCodec.encode(key_name)
		
		# Query data
		cur = self.cursor()
		cur.execute("SELECT data, file_ids FROM object_data WHERE key=?", (key,))
		result = cur.fetchone()
		if not result:
			raise KeyError(key_name)
		
		# Validate data
		data, file_ids = result
		assert file_ids is None  #XXX: TODO
		
		# Parse data
		decompressed = snappy.decompress(data)
		reader = mozserial.Reader(io.BufferedReader(io.BytesIO(decompressed)))
		return reader.read()
