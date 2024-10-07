import sys
import pathlib
from mozidbedit import mozidb
import json

def remove_notImplemented(obj: dict):
    if NotImplemented in obj.values():
        for k, v in obj.items():
            if v == NotImplemented:
                obj[k] = None

def read_objects(sitebase):
    dbs = {}
    items = {}
    for db_path in sitebase.iterdir():
        if not db_path.name.endswith(".sqlite"):
            continue
        with mozidb.IndexedDB(db_path) as conn:
            db_name = conn.get_name()
            if db_name is not None:
                dbs[db_name] = db_path
            # if db_name not in ["places_idb_store", "sms"]:
            #     continue
            try:
                conn.execute('alter table object_data add column json_data TEXT')
            except:
                pass
            items = conn.list_objects()
            update_data_bin = []
            update_data_text = []
            for key in items:
                try:
                    obj = conn.read_object(key_name=key)
                    remove_notImplemented(obj)
                    try:
                        value = json.dumps(obj)
                    except Exception as ne:
                        print(ne)
                        continue
                    if type(key) == str:
                        _key = mozidb.KeyCodec.encode(key)
                        update_data_text.append((value, _key))
                    else:
                        update_data_bin.append((value, key))
                except Exception as e:
                    print(e)
                    pass
            try:
                if len(update_data_bin) > 0:
                    conn.executemany('''
                                    UPDATE object_data set json_data = ? WHERE key = ?''',
                                    update_data_bin)
                if len(update_data_text) > 0:
                    conn.executemany('''
                                    UPDATE object_data set json_data = ? WHERE key like ?''',
                                    update_data_text)
            except:
                pass

if __name__ == "__main__":    
    if len(sys.argv) < 2:
        # storage_path=r'G:\Evidences\US_Supports\Project.proj\Project.proj\FileSystem\g0ruipep.default\storage\permanent\chrome\idb'
        print('idb folder missing!')
        exit()
    else:
        storage_path = sys.argv[1]
        print(storage_path)
    sitebase = pathlib.Path(storage_path)
    read_objects(sitebase=sitebase)
    print('done')