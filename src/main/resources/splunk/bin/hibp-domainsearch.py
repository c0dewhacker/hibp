import os
import sys
import csv
import json
import requests
from splunk.rest import simpleRequest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lib"))
from splunklib.modularinput import Script, Scheme, Argument, Event, EventWriter

class Input(Script):
    APP = "hibp"

    def get_scheme(self):
        scheme = Scheme("HIBP Domain Search")
        scheme.description = "Retrieves Have I Been Pwned Domain Search data"
        scheme.use_external_validation = False
        scheme.streaming_mode_xml = True
        scheme.use_single_instance = True

        return scheme
    
    def update_lookup(self, ew):
        # Request latest breach
        with requests.get("https://haveibeenpwned.com/api/v3/latestbreach") as r:
            if not r.ok:
                ew.log(EventWriter.ERROR, f"https://haveibeenpwned.com/api/v3/latestbreach returned {r.status_code}")
                return
            latestbreach = r.json()['Name']

        with open(os.path.join(self._input_definition.metadata["checkpoint_dir"],"lastestbreach"), "a+") as f:
            if latestbreach == f.read():
                ew.log(EventWriter.INFO, f"Latest breach hasnt changed from {latestbreach}")
                return
            f.seek(0)
            f.write(latestbreach)
            f.truncate()
        
        # Request all breaches
        with requests.get("https://haveibeenpwned.com/api/v3/breaches") as r:
            if not r.ok:
                ew.log(EventWriter.ERROR, f"https://haveibeenpwned.com/api/v3/breaches returned {r.status_code}")
                return
            breaches = r.json()

        #Update Lookup
        pass


    def stream_events(self, inputs, ew):
        self.service.namespace["app"] = self.APP
        # Get Variables
        input_name, input_items = inputs.inputs.popitem()
        kind, name = input_name.split("://")

        self.update_lookup(ew)

        apikeys = [
            x
            for x in self.service.storage_passwords
            if x.realm == "hibp"
        ]

        for apikey in apikeys:
            with requests.get("https://haveibeenpwned.com/api/v3/breacheddomain/{x.username}") as r:
                if not r.ok:
                    continue

        # Checkpoint
        checkpointfile = os.path.join(
            self._input_definition.metadata["checkpoint_dir"],
            f"{name}.json",
        )

        try:
            with open(checkpointfile, "r") as f:
                prev_done = json.load(f)
        except:
            prev_done = []
        next_done = []

        # Get Data
        with requests.session() as s:
            s.headers.update({"Authorization": f"Token {api_key}"})
            with s.get(
                f"https://{server}.cymru.com/api/jobs?group_id={group_id}"
            ) as jobs_response:
                if not jobs_response.ok:
                    ew.log(
                        EventWriter.ERROR,
                        f'Failed to get jobs from group_id={group_id} group_name={name} status=${job_response.status_code} response="{jobs_response.text}"',
                    )
                    return
                jobs = jobs_response.json()["data"]

                if len(jobs) == 0:
                    ew.log(
                        EventWriter.INFO,
                        f"No jobs found in group_id={group_id} group_name={name}",
                    )
                    return

                for job in jobs:
                    job_id = job["id"]

                    # Check Job
                    if job["status"] != "Completed":
                        ew.log(
                            EventWriter.INFO,
                            f"Skipping job={job_id} in group_id={group_id} group_name={name} because it hasnt finished",
                        )
                        continue
                    next_done.append(job_id)
                    if job_id in prev_done:
                        ew.log(
                            EventWriter.INFO,
                            f"Skipping job={job_id} in group_id={group_id} group_name={name} since we have downloaded it already",
                        )
                        continue
                    prev_done.append(job_id)

                    # Update Lookup
                    try:
                        lookup = f"{os.getenv('SPLUNK_HOME')}/etc/apps/TA-cyber_recon/lookups/{name}_iplist.csv"
                        queries = json.loads(job["input"])["queries"]
                        # Handle queries being a dict or list
                        if type(queries) is dict:
                            queries = queries.values()
                        ips = set(
                            itertools.chain.from_iterable(
                                [
                                    query.get("any_ip_addr", "").split(",")
                                    for query in queries
                                    if "any_ip_addr" in query
                                ]
                            )
                        )

                        with open(
                            lookup,
                            "a",
                        ) as f:
                            for ip in ips:
                                if len(ip.split(".")) != 4:
                                    continue
                                if "/" not in ip:
                                    ip += "/32"
                                f.write(
                                    f'\n"{ip}","{job["name"]}","{name}","CIDR","",""'
                                )
                    except Exception as e:
                        ew.log(
                            EventWriter.ERROR,
                            f'Failed to update lookup={lookup} for job={job_id} group_name={name} error="{e}"',
                        )

                    # Download Data
                    with s.get(
                        f"https://{server}.cymru.com/api/jobs/{job_id}?format=json",
                        stream=True,
                    ) as job_response:
                        if not job_response.ok:
                            ew.log(
                                EventWriter.ERROR,
                                f'Failed to get job={job_id} group_id={group_id} group_name={name} status=${job_response.status_code} response="{job_response.text}"',
                            )
                            continue
                        for line in job_response.iter_lines():
                            ew.write_event(
                                Event(
                                    index=name,
                                    host=job["name"],
                                    data=line.decode("utf-8"),
                                    source=f"{server}.cymru.com/api/jobs/{job_id}",
                                )
                            )

                    # Update checkpoint incase we crash
                    with open(checkpointfile, "w") as f:
                        json.dump(prev_done, f)

        # Update checkpoint with only the jobs still in the API response
        with open(checkpointfile, "w") as f:
            json.dump(next_done, f)


if __name__ == "__main__":
    exitcode = Input().run(sys.argv)
    sys.exit(exitcode)
