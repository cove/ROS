# LA County Record of Survey watch

Checks the official LA County parcel and Record of Survey REST services for
446 E Poppyfields Dr (AINs `5841018004`, `5841018005`) and 454 E Poppyfields Dr
(AIN `5841018003`). A known filed ROS at 426 E Poppyfields Dr (AIN
`5841018007`, RS 370-015) must be retrieved first. If a County request or
control fails, the program exits with status 2 and reports a **lookup failure**,
never “no ROS found.”

Run `python3 ros_watch.py` to print the complete JSON result. The script is
intended for a ChatGPT Work scheduled task through December 23, 2026; report
newly found surveys or lookup failures, and remain silent on unchanged negative
results. Work task email delivery depends on ChatGPT notification settings.

The County GIS survey layer indexes polygon intersections. “No ROS found”
means no intersecting feature appeared in that layer at query time; it does
not prove that a recently filed record has already been indexed.

- [County parcel layer](https://public.gis.lacounty.gov/public/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0)
- [County ROS layer](https://dpw.gis.lacounty.gov/dpw/rest/services/landrecords_mapviewer/MapServer/6)
- [County Land Records viewer](https://dpw.lacounty.gov/sur/landrecords/#map)