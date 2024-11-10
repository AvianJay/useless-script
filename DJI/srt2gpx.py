# My code is shit.
from datetime import datetime
import re, sys

def srt_to_gpx(srt_file, gpx_file):
    # create gpx
    with open(gpx_file, 'w') as gpx:
        gpx.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        gpx.write('<gpx version="1.1" creator="AvianJay"><metadata><name>DJI Drown</name><desc>Converted with AvianJay's Tool</desc></metadata>\n')
        gpx.write('  <trk>\n')
        gpx.write('    <name>DJI Drone Flight</name>\n')
        gpx.write('    <trkseg>\n')

        # read srt
        with open(srt_file, 'r') as srt:
            str_list = srt.read().split("\n\n")
            str_list.remove(str_list[-1])
            for line in str_list:
                # match
                match = re.search(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+', line)
                if match:
                    timestamp = match.group(0)
                    # convert time format
                    timestamp = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S.%f')
                    timestamp = timestamp.isoformat() + 'Z'

                    # get some data from srt
                    lat_match = re.search(r'latitude: ([\d.]+)', line)
                    lon_match = re.search(r'longitude: ([\d.]+)', line)
                    alt_match = re.search(r'rel_alt: ([\d.]+)', line)

                    if lat_match and lon_match and alt_match:
                        latitude = lat_match.group(1)
                        longitude = lon_match.group(1)
                        altitude = alt_match.group(1)

                        # write data into gpx file
                        gpx.write(f'      <trkpt lat="{latitude}" lon="{longitude}">\n')
                        gpx.write(f'        <ele>{altitude}</ele>\n')
                        gpx.write(f'        <time>{timestamp}</time>\n')
                        gpx.write('      </trkpt>\n')

        # end of gpx
        gpx.write('    </trkseg>\n')
        gpx.write('  </trk>\n')
        gpx.write('</gpx>\n')

if __name__ == "__main__":
    if len(sys.argv) == 2:
        print("開始轉換...")
        srt_to_gpx(sys.argv[1], sys.argv[1].replace(sys.argv[1].split(".")[-1], "gpx"))
        print("成功將檔案輸出到", sys.argv[1].replace(sys.argv[1].split(".")[-1], "gpx"))
    else:
        print("Usage:", sys.argv[0], "[Filename]")
