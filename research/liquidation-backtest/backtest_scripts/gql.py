import sys, json, requests
def gql(q, v=None):
    r = requests.post("https://api.morpho.org/graphql", json={"query": q, "variables": v or {}}, timeout=60)
    j = r.json()
    if "errors" in j: raise RuntimeError(json.dumps(j["errors"])[:2000])
    return j["data"]
if __name__ == "__main__":
    print(json.dumps(gql(sys.argv[1]), indent=1)[:int(sys.argv[2]) if len(sys.argv)>2 else 6000])
