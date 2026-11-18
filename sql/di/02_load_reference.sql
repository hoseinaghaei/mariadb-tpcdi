-- Phase 4: the reference tables and DimDate/DimTime.
--
-- Clause 1.3.7: these are loaded once during the Historical Load and never
-- modified again.  Clause 4.4.1 note: DimDate and DimTime are static
-- dimensions -- no history tracking, no surrogate key generation, the file
-- already carries the key.
--
-- __STAGING__ is replaced with the batch directory by scripts/load-reference.sh
-- so one file serves every scale factor.
--
-- TWO THINGS THE GENERATOR DOES THAT THE SPECIFICATION DOES NOT PROMISE
--
-- 1. Every line ends CRLF, because PDGF/Constants.properties sets
--    DEFAULT_LINESEPARATOR=CRLF.  Hence LINES TERMINATED BY '\r\n'.  Load
--    these files with the default '\n' and the last column of every row
--    silently gains a trailing carriage return -- TX_RATE becomes '0.15000\r',
--    which MariaDB will either reject or truncate depending on sql_mode.
--
-- 2. Booleans arrive as the words 'true' and 'false'.  Clause 2.2.1
--    Table 2.2.1 says BOOLEAN is "0" for False and "1" for True in the source
--    files.  The generator disagrees with the specification here, so the three
--    flag columns are mapped explicitly rather than assigned.

-- ---------------------------------------------------------------- DimDate
-- Clause 2.2.2.7 / 4.5.5.  18 fields, same order as Clause 3.2.5.
LOAD DATA INFILE '__STAGING__/Date.txt'
  INTO TABLE DimDate
  FIELDS TERMINATED BY '|'
  LINES TERMINATED BY '\r\n'
  (SK_DateID, DateValue, DateDesc, CalendarYearID, CalendarYearDesc,
   CalendarQtrID, CalendarQtrDesc, CalendarMonthID, CalendarMonthDesc,
   CalendarWeekID, CalendarWeekDesc, DayOfWeekNum, DayOfWeekDesc,
   FiscalYearID, FiscalYearDesc, FiscalQtrID, FiscalQtrDesc, @HolidayFlag)
  SET HolidayFlag = (@HolidayFlag = 'true');

-- ---------------------------------------------------------------- DimTime
-- Clause 2.2.2.15 / 4.5.7.  10 fields, same order as Clause 3.2.7.
LOAD DATA INFILE '__STAGING__/Time.txt'
  INTO TABLE DimTime
  FIELDS TERMINATED BY '|'
  LINES TERMINATED BY '\r\n'
  (SK_TimeID, TimeValue, HourID, HourDesc, MinuteID, MinuteDesc,
   SecondID, SecondDesc, @MarketHoursFlag, @OfficeHoursFlag)
  SET MarketHoursFlag = (@MarketHoursFlag = 'true'),
      OfficeHoursFlag = (@OfficeHoursFlag = 'true');

-- --------------------------------------------------------------- Industry
-- Clause 2.2.2.11 / 4.5.13.
LOAD DATA INFILE '__STAGING__/Industry.txt'
  INTO TABLE Industry
  FIELDS TERMINATED BY '|'
  LINES TERMINATED BY '\r\n'
  (IN_ID, IN_NAME, IN_SC_ID);

-- ------------------------------------------------------------- StatusType
-- Clause 2.2.2.13 / 4.5.16.
LOAD DATA INFILE '__STAGING__/StatusType.txt'
  INTO TABLE StatusType
  FIELDS TERMINATED BY '|'
  LINES TERMINATED BY '\r\n'
  (ST_ID, ST_NAME);

-- ---------------------------------------------------------------- TaxRate
-- Clause 2.2.2.14 / 4.5.17.
LOAD DATA INFILE '__STAGING__/TaxRate.txt'
  INTO TABLE TaxRate
  FIELDS TERMINATED BY '|'
  LINES TERMINATED BY '\r\n'
  (TX_ID, TX_NAME, TX_RATE);

-- -------------------------------------------------------------- TradeType
-- Clause 2.2.2.18 / 4.5.18.
LOAD DATA INFILE '__STAGING__/TradeType.txt'
  INTO TABLE TradeType
  FIELDS TERMINATED BY '|'
  LINES TERMINATED BY '\r\n'
  (TT_ID, TT_NAME, TT_IS_SELL, TT_IS_MRKT);
