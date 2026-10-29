import json
from operator import itemgetter


class json2attribute:

    def __init__(self, cfg_file):
        try:
            with open(cfg_file) as f:
                cfg_data = json.loads(f.read())
            self.attr_lookup = {}
            for record in cfg_data["G2_CONFIG"]["CFG_ATTR"]:
                self.attr_lookup[record["ATTR_CODE"]] = record
            self.feature_lookup = {}
            for record in cfg_data["G2_CONFIG"]["CFG_FTYPE"]:
                self.feature_lookup[record["FTYPE_CODE"]] = record
        except Exception as ex:
            raise Exception(ex)

    def parse(self, json_string, rtn_value="attr_list"):
        try:
            json_data = json.loads(json_string)
        except Exception as ex:
            raise Exception(ex)

        self.attr_groups = {}
        for attribute in (x for x in json_data if json_data[x]):
            if isinstance(json_data[attribute], list):
                i = 0
                for child_data in json_data[attribute]:
                    i += 1
                    for record_attribute in (x for x in child_data if child_data[x]):
                        attr_data = self.lookup_attribute(record_attribute.upper(), child_data[record_attribute])
                        segment_id = f"{attribute}-{i}"
                        self.update_grouping(segment_id, attr_data)
            else:
                attr_data = self.lookup_attribute(attribute.upper(), json_data[attribute])
                segment_id = "ROOT"
                self.update_grouping(segment_id, attr_data)

        if rtn_value == "attr_groups":
            return self.attr_groups

        self.attr_list = []
        for segment_id in self.attr_groups:
            segment, attribute, usage_type = segment_id.split("|")
            min_attr_id = 9999
            attr_values = []
            attr_json = {}
            used_from_date = used_thru_date = None
            for attr_data in sorted(self.attr_groups[segment_id], key=itemgetter("ATTR_ID")):
                if attr_data.get("ATTR_ID") < min_attr_id:
                    min_attr_id = attr_data.get("ATTR_ID")
                if attr_data.get("FELEM_CODE") == "USAGE_TYPE":
                    usage_type = attr_data["ATTR_VALUE"]
                elif attr_data.get("FELEM_CODE") == "USED_FROM_DT":
                    used_from_date = attr_data["ATTR_VALUE"]
                elif attr_data.get("FELEM_CODE") == "USED_THRU_DT":
                    used_thru_date = attr_data["ATTR_VALUE"]
                # elif attr_data.get('FELEM_CODE') == 'KEY_TYPE' and attribute == 'REL_POINTER':
                #    continue # simply ignoring optional domain for rel_pointers
                else:
                    attr_values.append(str(attr_data["ATTR_VALUE"]))
                    attr_json[attr_data["ATTR_CODE"]] = attr_data["ATTR_VALUE"]
            self.attr_list.append(
                {
                    "SEGMENT": segment,
                    "ATTR_ID": min_attr_id,
                    "ATTRIBUTE": attribute,
                    "FTYPE_CODE": attr_data.get("FTYPE_CODE"),
                    "ATTR_VALUE": " ".join(attr_values),
                    "USAGE_TYPE": usage_type,
                    "USED_FROM_DT": used_from_date,
                    "USED_THRU_DT": used_thru_date,
                    "ATTR_JSON": attr_json,
                }
            )
        return self.attr_list

    def lookup_attribute(self, attr_name, attr_value):
        attr_data = {"ATTR_ID": 9999, "ATTR_CODE": attr_name, "ATTR_CLASS": "PAYLOAD"}
        if attr_name in self.attr_lookup:
            attr_data = self.attr_lookup[attr_name].copy()
        elif "_" in attr_name:
            possible_label = attr_name[0 : attr_name.find("_")]
            possible_attr_name = attr_name[attr_name.find("_") + 1 :]
            if possible_attr_name in self.attr_lookup:
                attr_data = self.attr_lookup[possible_attr_name].copy()
                attr_data["USAGE_TYPE"] = possible_label
            else:
                possible_label = attr_name[attr_name.rfind("_") + 1 :]
                possible_attr_name = attr_name[0 : attr_name.rfind("_")]
                if possible_attr_name in self.attr_lookup:
                    attr_data = self.attr_lookup[possible_attr_name].copy()
                    attr_data["USAGE_TYPE"] = possible_label
        attr_data["ATTR_VALUE"] = attr_value
        return attr_data

    def update_grouping(self, segment_id, attr_data):
        if attr_data.get("FTYPE_CODE"):
            attribute = attr_data.get("FTYPE_CODE")
        else:
            attribute = attr_data.get("ATTR_CODE")
        segment_id += f"|{attribute}|{attr_data.get('USAGE_TYPE','')}"
        if segment_id not in self.attr_groups:
            self.attr_groups[segment_id] = [attr_data]
        else:
            self.attr_groups[segment_id].append(attr_data)
