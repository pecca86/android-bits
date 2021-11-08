"""
Copyright 2021, CCL Forensics
Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:
The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

import base64
import struct
import typing
import xml.etree.ElementTree as etree


__version__ = "0.0.1"
__description__ = "Python module to convert Android ABX binary XML files"
__contact__ = "Alex Caithness"

# See: base/core/java/com/android/internal/util/BinaryXmlSerializer.java


class AbxReader:
    MAGIC = b"ABX\x00"

    # These first constants are from: libcore/xml/src/main/java/org/xmlpull/v1/XmlPullParser.java
    # most of them are unused, but here for completeness
    START_DOCUMENT = 0
    END_DOCUMENT = 1
    START_TAG = 2
    END_TAG = 3
    TEXT = 4
    CDSECT = 5
    ENTITY_REF = 6
    IGNORABLE_WHITESPACE = 7
    PROCESSING_INSTRUCTION = 8
    COMMENT = 9
    DOCDECL = 10

    ATTRIBUTE = 15

    TYPE_NULL = 1 << 4
    TYPE_STRING = 2 << 4
    TYPE_STRING_INTERNED = 3 << 4
    TYPE_BYTES_HEX = 4 << 4
    TYPE_BYTES_BASE64 = 5 << 4
    TYPE_INT = 6 << 4
    TYPE_INT_HEX = 7 << 4
    TYPE_LONG = 8 << 4
    TYPE_LONG_HEX = 9 << 4
    TYPE_FLOAT = 10 << 4
    TYPE_DOUBLE = 11 << 4
    TYPE_BOOLEAN_TRUE = 12 << 4
    TYPE_BOOLEAN_FALSE = 13 << 4

    def _read_raw(self, length):
        buff = self._stream.read(length)
        if len(buff) < length:
            raise ValueError(f"couldn't read enough data at offset: {self._stream.tell() - len(buff)}")
        return buff

    def _read_byte(self):
        buff = self._read_raw(1)
        return buff[0]

    def _read_short(self):
        buff = self._read_raw(2)
        return struct.unpack(">h", buff)[0]

    def _read_int(self):
        buff = self._read_raw(4)
        return struct.unpack(">i", buff)[0]

    def _read_long(self):
        buff = self._read_raw(8)
        return struct.unpack(">q", buff)[0]

    def _read_float(self):
        buff = self._read_raw(4)
        return struct.unpack(">f", buff)[0]

    def _read_double(self):
        buff = self._read_raw(8)
        return struct.unpack(">d", buff)[0]

    def _read_string_raw(self):
        length = self._read_short()
        if length < 0:
            raise ValueError(f"Negative string length at offset {self._stream.tell() - 2}")
        buff = self._read_raw(length)
        return buff.decode("utf-8")

    def _read_interned_string(self):
        reference = self._read_short()
        if reference == -1:
            value = self._read_string_raw()
            self._interned_strings.append(value)
        else:
            value = self._interned_strings[reference]
        return value

    def __init__(self, stream: typing.BinaryIO):
        self._interned_strings = []
        self._stream = stream

    def read(self):
        magic = self._read_raw(len(AbxReader.MAGIC))
        if magic != AbxReader.MAGIC:
            raise ValueError(f"Invalid magic. Expected {AbxReader.MAGIC.hex()}; got: {magic.hex()}")

        document_opened = False
        root_closed = False
        root = None
        element_stack = []  # because ElementTree doesn't support parents we maintain a stack

        while True:
            # Read the token. This gives us the XML data type and the raw data type.
            token_raw = self._stream.read(1)
            if not token_raw:
                break
            token = token_raw[0]

            data_start_offset = self._stream.tell()

            # The lower nibble gives us the XML type. This is mostly defined in XmlPullParser.java, other than
            # AATRIBUTE which is from BinaryXmlSerializer
            xml_type = token & 0x0f
            if xml_type == AbxReader.START_DOCUMENT:
                assert token & 0xf0 == AbxReader.TYPE_NULL
                if document_opened:
                    raise ValueError(f"Unexpected START_DOCUMENT at offset {self._stream.tell()}")
                document_opened = True

            elif xml_type == AbxReader.END_DOCUMENT:
                assert token & 0xf0 == AbxReader.TYPE_NULL
                assert len(element_stack) == 0
                assert document_opened
                break

            elif xml_type == AbxReader.START_TAG:
                assert token & 0xf0 == AbxReader.TYPE_STRING_INTERNED
                assert document_opened
                assert not root_closed

                tag_name = self._read_interned_string()
                if len(element_stack) == 0:
                    element = etree.Element(tag_name)
                    element_stack.append(element)
                    root = element
                else:
                    element = etree.SubElement(element_stack[-1], tag_name)
                    element_stack.append(element)

            elif xml_type == AbxReader.END_TAG:
                assert token & 0xf0 == AbxReader.TYPE_STRING_INTERNED
                assert len(element_stack) >= 0

                tag_name = self._read_interned_string()
                if element_stack[-1].tag != tag_name:
                    raise ValueError(
                        f"Unexpected END_TAG name at {data_start_offset}. "
                        f"Expected: {element_stack[-1].tag}; got: {tag_name}")

                last = element_stack.pop()
                if len(element_stack) == 0:
                    root_closed = True
                    root = last
            elif xml_type == AbxReader.TEXT:
                value = self._read_string_raw()
                raise NotImplementedError()  # don't know how to best account for text vs tail yet
            elif xml_type == AbxReader.ATTRIBUTE:
                assert len(element_stack) >= 0

                attribute_name = self._read_interned_string()

                assert attribute_name not in element_stack[-1].attrib

                data_type = token & 0xf0

                if data_type == AbxReader.TYPE_NULL:
                    value = None  # remember to output xml as "null"
                elif data_type == AbxReader.TYPE_BOOLEAN_TRUE:
                    value = True  # remember to output xml as "true"
                elif data_type == AbxReader.TYPE_BOOLEAN_FALSE:
                    value = False  # remember to output xml as "false"
                elif data_type == AbxReader.TYPE_INT:
                    value = self._read_int()
                elif data_type == AbxReader.TYPE_INT_HEX:
                    value = f"{self._read_int():x}"  # don't do this conversion in dict
                elif data_type == AbxReader.TYPE_LONG:
                    value = self._read_long()
                elif data_type == AbxReader.TYPE_LONG_HEX:
                    value = f"{self._read_int():x}"  # don't do this conversion in dict
                elif data_type == AbxReader.TYPE_FLOAT:
                    value = self._read_float()
                elif data_type == AbxReader.TYPE_DOUBLE:
                    value = self._read_double()
                elif data_type == AbxReader.TYPE_STRING:
                    value = self._read_string_raw()
                elif data_type == AbxReader.TYPE_STRING_INTERNED:
                    value = self._read_interned_string()
                elif data_type == AbxReader.TYPE_BYTES_HEX:
                    length = self._read_short()  # is this safe?
                    value = self._read_raw(length)
                    value = value.hex()  # skip this step for dict
                elif data_type == AbxReader.TYPE_BYTES_BASE64:
                    length = self._read_short()  # is this safe?
                    value = self._read_raw(length)
                    value = base64.encodebytes(value).decode().strip()
                else:
                    raise ValueError(f"Unexpected datatype at offset: {data_start_offset}")

                element_stack[-1].attrib[attribute_name] = str(value)
            else:
                raise NotImplementedError(f"unexpected XML type: {xml_type}")

        assert root_closed
        assert root is not None
        tree = etree.ElementTree(root)

        return tree


def main(args):
    in_path = pathlib.Path(args[0])
    with in_path.open("rb") as f:
        reader = AbxReader(f)
        doc = reader.read()

    print(etree.tostring(doc.getroot()).decode())


if __name__ == '__main__':
    import sys
    import pathlib
    main(sys.argv[1:])
