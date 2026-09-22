#! /usr/bin/env python3
# vim:fenc=utf-8
#
# Copyright © 2026 fred <github-fred@hidzz.com>
#
# Distributed under terms of the BSD 3-Clause license.

import argparse
import logging
import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import adif_parser
import dxcty_parser
import folium
import numpy as np
import pandas as pd
import pyproj
from folium.plugins import Geocoder, HeatMap, MarkerCluster
from geo_ham import ddm2decimal, grid2latlon
from jinja2 import Environment, FileSystemLoader

__all__ = ["main", "load_adif", "render_html"]

logger = logging.getLogger("analog")

KEEP_FIELDS = [
  "CALL", "BAND", "MODE", "COUNTRY", "LAT", "LON",
  "GRIDSQUARE", "QSO_DATE", "TIME_ON", "TX_PWR",
]

NEEDED_FIELDS = [
  "CALL", "BAND", "MODE", "GRIDSQUARE", "QSO_DATE",
]

MAP_MODES = {
  'USB': 'SSB',
  'LSB': 'SSB',
  'BPSK31': 'PSK',
  'BPSK64': 'PSK',
  'FELDHELL': 'HELL',
  'MFSK': 'FT4',
  'OLIVIA-8/250': 'OLIVIA',
  'OLIVIA-8/500': 'OLIVIA',
  'QPSK': 'PSK',
  'TOR': 'THOR',
  'THORMICRO': 'THOR',
  'DATA-FM': 'FM',
  'DIGITALVOICE': 'DV',
}

GRADIENT = {
    0.0: '#00ffff',  # Cyan - lowest density
    0.2: '#0000ff',  # Blue
    0.4: '#00ff00',  # Green
    0.6: '#ffff00',  # Yellow
    0.8: '#ff8800',  # Orange
    1.0: '#ff0000'   # Red - highest density
}


@dataclass(slots=True)
class Contact:
  # pylint: disable=too-many-instance-attributes
  call: str
  band: float
  mode: str
  country: str
  lat: float
  lon: float
  gridsquare: str
  tx_pwr: float
  date: datetime
  distance: float

  def __str__(self) -> str:
    lines = [
      f'<big><a href="https://qrz.com/db/{self.call}" target="_qrz">'
      f'<b>{self.call}</b></a></big>',
    ]

    def add(label: str, value) -> None:
      if value:
        lines.append(f"<b>{label}</b>:&nbsp;{value}")

    add("Country", self.country.replace(" ", "&nbsp;") if self.country else None)
    add("Mode", self.mode)
    add("Date", self.date.strftime("%Y-%m-%d"))
    add("Time", self.date.strftime("%H:%M"))
    add("Band", self.band)

    if pd.notna(self.distance):
      add("Distance", f"{self.distance / 1000:.1f}km")

    add("TX Power", f"{self.tx_pwr}W" if self.tx_pwr else None)
    return re.sub(r' +|\n +', replace_nl, "\n".join(lines).strip())


class DXE:
  # pylint: disable=too-few-public-methods
  _dxe = None

  @classmethod
  def lookup(cls, call):
    if cls._dxe is None:
      cls._dxe = dxcty_parser.load_cty()
    dxe = cls._dxe.lookup(call)
    return dxe.entity if dxe else None


def replace_nl(match: re.Match) -> str:
  if match.group().startswith('\n'):
    return '<br>'
  return ' '


def display_markers(wmap: folium.FeatureGroup, contacts: pd.DataFrame) -> None:
  marker_cluster = MarkerCluster().add_to(wmap)
  for _, row in contacts.iterrows():
    contact = Contact(*row.values)
    match contact.mode:
      case 'FT8' | 'FT4' | 'JT65':
        color = 'orange'
        icon = 'computer-mouse'
      case 'CW':
        color = 'gray'
        icon = 'fingerprint'
      case 'SSB':
        color = 'blue'
        icon = 'headset'
      case 'RTTY':
        color = 'darkblue'
        icon = 'tty'
      case 'PSK' | 'THOR' | 'OLIVIA' | 'CONTESTI':
        color = 'cadetblue'
        icon = 'keyboard'
      case _:
        color = 'beige'
        icon = 'tower-broadcast'

    try:
      folium.Marker(
        location=[contact.lat, contact.lon],
        icon=folium.Icon(color=color, prefix='fa', icon=icon),
        popup=str(contact)
      ).add_to(marker_cluster)
    except ValueError:
      logger.error('ValueError (%f, %f) %s %s', contact.lat, contact.lon,
                   contact.call, contact.mode)


def get_info(row: pd.Series) -> pd.Series:
  dxe = DXE.lookup(row['CALL'])
  if dxe is None:
    row['CALL'] = None
    return row

  try:
    if pd.notna(row['LAT']) and pd.notna(row['LON']):
      row['LAT'] = ddm2decimal(row['LAT'])
      row['LON'] = ddm2decimal(row['LON'])
    elif pd.notna(row.get('GRIDSQUARE', np.nan)):
      row['LAT'], row['LON'] = grid2latlon(row.GRIDSQUARE)
    else:
      row['LAT'], row['LON'] = dxe.latitude, dxe.longitude * -1
  except (TypeError, ValueError):
    logger.warning('Cannot calculate the gps coords for: %s', row['CALL'])
    row['CALL'] = None
    return row

  if not row.get('COUNTRY'):
    row['COUNTRY'] = dxe.country

  return row


def load_adif(filename: Path) -> pd.DataFrame:
  """ Load the adif file"""

  with filename.open('r', encoding='utf-8', errors='replace') as fd:
    adif = adif_parser.ParseADIF(fd)

  data = pd.DataFrame(adif.contacts)
  missing_fields = list(set(NEEDED_FIELDS) - set(data.columns))

  if missing_fields:
    raise ValueError(
      f'The following fields are missing from the ADIF file: {", ".join(sorted(missing_fields))}'
    )

  keep_fields = list(set(data.columns) & set(KEEP_FIELDS))
  data = data[keep_fields]

  data[missing_fields] = np.nan
  return data.copy()


def normalize_data(data: pd.DataFrame, location: tuple[float, float]) -> pd.DataFrame:
  """ Gets an adif object, cleanup the data. Normalize the data and
  calculated the distance and azimuth """

  data['CALL'] = data.CALL.astype(str)
  data['MODE'] = data.MODE.replace(MAP_MODES)
  data['MODE'] = data.MODE.replace({k: 'Other' for k in data.MODE.value_counts().index[9:]})
  data['QSO_DATE'] = data['QSO_DATE'].fillna('19710101')
  data['TIME_ON'] = data['TIME_ON'].fillna('000000')
  data['START_TIME'] = pd.to_datetime(data[['QSO_DATE', 'TIME_ON']].agg(' '.join, axis=1))

  if 'COUNTRY' not in data.columns:
    data['COUNTRY'] = np.nan
  data['COUNTRY'] = data['COUNTRY'].fillna('')

  if 'TIME_ON' not in data.columns:
    data['TIME_ON'] = '000000'

  if 'LAT' not in data.columns:
    data['LAT'] = np.nan

  if 'LON' not in data.columns:
    data['LON'] = np.nan

  data = data.apply(get_info, axis=1)
  data = data[~data.CALL.isnull()]
  if data.empty:
    raise ValueError('No valid records')

  data['LAT'] = pd.to_numeric(data['LAT'])
  data['LON'] = pd.to_numeric(data['LON'])

  if 'TX_PWR' not in data.columns:
    data['TX_PWR'] = np.nan
  data['TX_PWR'] = data['TX_PWR'].fillna(0.0)

  # calculate distance and azimuth
  geod = pyproj.Geod(ellps='WGS84')
  orig = np.full((data.shape[0], 2), location)
  data['LAT'] = pd.to_numeric(data['LAT'], errors='coerce').fillna(0).astype('float64')
  data['LON'] = pd.to_numeric(data['LON'], errors='coerce').fillna(0).astype('float64')
  distance = np.zeros(data.shape[0])
  distance = np.array(geod.inv(orig[:, 1], orig[:, 0],  data.LON.values, data.LAT.values))
  data[['AZIMUTH', 'INV', 'DISTANCE']] = np.transpose(distance)
  data = data.reset_index(drop=True)

  return data.copy()


def plot_map(data: pd.DataFrame, call: str, location: tuple[float, float]) -> str:

  attr = ('&copy; <a href="https://www.OpenStreetMap.org/copyright">OpenStreetMap</a> '
          'contributors Fred <a href="https://qrz.com/db/W6BSD">W6BSD</a>')

  wmap = folium.Map(location=location, zoom_start=3, tiles=None)

  folium.TileLayer(
    tiles='https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    name='OpenStreet Map',
    attr=attr
  ).add_to(wmap)

  Geocoder(add_marker=False, zoom=8, provider="photon").add_to(wmap)
  folium.Marker(location, popup=f'<b><i>Calling Station: {call}</i></b>'.replace(' ', '&nbsp'),
                icon=folium.Icon(prefix='fa', icon='walkie-talkie', color='red')).add_to(wmap)

  modes = (
    data['MODE']
    .value_counts()
    .reset_index()
    .sort_values('MODE')
  )

  for row in modes.itertuples():
    layer = folium.FeatureGroup(name=f'{row.MODE} ({row.count})')
    c_mode = data[data.MODE == row.MODE]
    HeatMap(
      data=c_mode[['LAT', 'LON']],
      radius=25, blur=20, min_opacity=0.2,
      max_zoom=15, gradient=GRADIENT,
    ).add_to(layer)

    display_markers(layer, c_mode)
    layer.add_to(wmap)

  folium.LayerControl().add_to(wmap)
  return wmap._repr_html_()  # pylint: disable=protected-access


def render_html(data: pd.DataFrame, call: str, location: tuple[float, float], output: Path):
  contacts = data[
    ["CALL", "BAND", "MODE", "COUNTRY", "LAT", "LON", "GRIDSQUARE", "TX_PWR",
     "START_TIME", "DISTANCE"]
  ]
  contacts = contacts.dropna().reset_index(drop=True)

  template_dir = Path(__file__).with_name('templates')
  static_dir = Path(__file__).with_name('static')
  env = Environment(loader=FileSystemLoader([template_dir, static_dir]))

  template = env.get_template('map.html')
  start = contacts.START_TIME.min()
  end = contacts.START_TIME.max()

  pmap = plot_map(contacts, call, location)
  title = f'QSOs for {call}'

  content = template.render(call=call, title=title, start=start, end=end,
                            count=contacts.shape[0], map=pmap, date=datetime.now(UTC))

  with output.open(mode="w", encoding="utf-8") as fout:
    fout.write(content)
    logging.info('Write file: %s', output)


def type_grid(parg: str) -> str:
  parg = parg.upper()
  if not re.match(r'^[A-R]{2}[0-9]{2}[A-X]{2}$', parg):
    raise argparse.ArgumentTypeError('Wrong maidenhead grid square')
  return parg


def main() -> None:
  logger.setLevel(logging.INFO)
  parser = argparse.ArgumentParser(description='QSO to map')
  parser.add_argument('-c', '--call', required=True,
                      help='Call sign')
  parser.add_argument('-f', '--adif-file', type=Path, required=True,
                      help='ADIF filename')
  parser.add_argument('-o', '--output', type=Path, required=True,
                      help='Destination filename')
  parser.add_argument('-g', '--grid', type=type_grid, required=True,
                      help='Maidenhead grid square AA00aa')
  opts = parser.parse_args()

  location = grid2latlon(opts.grid)

  try:
    adif = load_adif(opts.adif_file)
    adif = adif[-10000:]
  except (FileNotFoundError, ValueError) as err:
    logger.error(err)
    sys.exit(os.EX_IOERR)

  try:
    adif = normalize_data(adif, location)
  except (ValueError, AttributeError) as err:
    logger.error(err)
    sys.exit(os.EX_PROTOCOL)

  render_html(adif, opts.call, location, opts.output)


if __name__ == "__main__":
  try:
    main()
  except KeyboardInterrupt:
    logger.error('Interrupted by user')
