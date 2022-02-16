# trakt-for-appletv
Trakt.tv scrobbler for Apple TV

Tested with tvOS 12, but earlier releases might also work. Scrobbling is currently supported for standard TV Shows and 
Movies apps, as well as Netflix and Amazon Prime.

Usage:
```
$ pip3 install -r requirements.txt
$ python3 ./tvscrobbler.py 
```

## Amazon Prime configuration
Scrobbling of Amazon Prime requires additional configuration. Specifically, the config.yml needs to contain the following section:
```
amazon:
  cookie: ubid-acbXX=...; x-acbXX=..; at-acbXX=..
  get_playback_resources_url: https://atv-ps-eu.amazon.de/cdp/catalog/GetPlaybackResources?asin=%s&consumptionType=Streaming&desiredResources=CatalogMetadata&deviceID=...&deviceTypeID=...&firmware=1&resourceUsage=CacheResources&videoMaterialType=Feature&clientId=...&titleDecorationScheme=primary-content&customerID=...&token=...
```

Where `XX` indicates a country code (e.g. `ubid-acbde`, `ubid-acbuk`, etc). The cookies and the GetPlaybackResources url need to be obtained by logging onto Amazon from the browser and then looking at the established cookies and the network activity during playback. The domain for the URL might be different depending on the country. Not the `%s` in the `asin=%s` parameter, which is used to substitute the actual value.
