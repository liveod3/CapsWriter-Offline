"""
Regular expression patterns.
"""

import re
from .mappings import common_units

# Select potential replacement spans.
pattern = re.compile(rf"""(?ix)
([a-z]\s*)?
(
  (?:
    [正负]?
    (
      [几零幺一二两三四五六七八九十百千万点比]
      |[零一二三四五六七八九十][ ]
      |(?<=[一二两三四五六七八九十])[年月日号分]
      |(分之)
    )+
    (
      (?<=[一二两三四五六七八九十])([a-zA-Z年月日号]|{common_units})
      |(?<=[一二两三四五六七八九十]\s)[a-zA-Z]
    )?
    (?(1)
    |(?(5)
      |(
        [零幺一二两三四五六七八九十百千万亿点比]
        |(分之)
      )
    )+
    )
  )
  |
  (?:
    [正负][零幺一二两三四五六七八九十百千万点]
  )
)
""")

