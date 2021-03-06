'''For logging status, address and data'''

from colorama import init, Fore, Back
init(autoreset=True) #to convert termcolor to wins color

from pathlib import Path
from os import mkdir, listdir, stat, SEEK_END
from os.path import exists, getsize, getmtime, join, isdir, getctime
from datetime import datetime
from time import time, sleep
from contextlib import suppress
from numpy import prod, mean, rad2deg, array, ndarray, float64
import inspect, json, wrapt, struct, geocoder, ast, socket
import netifaces as nif
from pandas import DataFrame
from tables import open_file, Filters, Float32Atom, Float64Atom, StringCol, IsDescription
from json import loads

# MAT SAVE & LOAD
from scipy.io import savemat, loadmat

from flask import session, g
from pyqum import get_db
from pyqum.instrument.toolbox import waveform, flatten

__author__ = "Teik-Hui Lee"
__copyright__ = "Copyright 2019, The Pyqum Project"
__credits__ = ["Chii-Dong Chen"]
__license__ = "GPL"
__version__ = "beta3"
__email__ = "teikhui@phys.sinica.edu.tw"
__status__ = "development"

pyfilename = inspect.getfile(inspect.currentframe()) # current pyscript filename (usually with path)
MAIN_PATH = Path(pyfilename).parents[7] / "HODOR" / "CONFIG"
INSTR_PATH = MAIN_PATH / "INSTLOG"
USR_PATH = MAIN_PATH / "USRLOG"
PORTAL_PATH = MAIN_PATH / "PORTAL"
ADDRESS_PATH = MAIN_PATH / "Address"
SPECS_PATH = MAIN_PATH / "SPECS"

# Pending: extract MAC from IP?
def mac_for_ip(ip):
    'Returns a list of MACs for interfaces that have given IP, returns None if not found'
    for i in nif.interfaces():
        addrs = nif.ifaddresses(i)
        try:
            if_mac = addrs[nif.AF_LINK][0]['addr']
            if_ip = addrs[nif.AF_INET][0]['addr']
        except(IndexError, KeyError): #ignore ifaces that dont have MAC or IP
            if_mac = if_ip = None
        if if_ip == ip:
            return if_mac
    return None

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # doesn't even have to be reachable
        s.connect(('10.255.255.255', 1))
        IP = s.getsockname()[0]
    except:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def location():
    place = []
    # approximate radius of earth in km
    eaRth = 6373.0
    # acceptable distance error in km
    toleratekm = 0.00000001
    toleratedeg = rad2deg(toleratekm / eaRth)
    g = geocoder.ip('me')
    gps = g.latlng #[latitude, longitude]
    try:
        if mean([abs(i-j) for i,j in zip(gps, [25.0478, 121.532])]) < toleratedeg:
            place.append('AS')
        if mean([abs(i-j) for i,j in zip(gps, [25.0478, 121.532])]) < toleratedeg*10:
            place.append('Taipei')
    except: 
        print("Location service may not be available. Please check.")
        pass
    
    details = {'Org':g.org, 'Location': [g.city, g.country], 'Host': g.hostname, 'IP': [get_local_ip(), g.ip], 'Coordinate': [g.lat, g.lng]}
    place.append(str(details))

    return place

def clocker(stage, prev=0):
    '''timing algorithm in seconds'''
    now = time()
    duration = now - prev
    if int(stage) > 0:
        print(Fore.BLUE + Back.WHITE + "It took {:.5f}s to complete {:d}-th stage\n".format(duration, stage))
    stage += 1
    return stage, now

def status_code(status):
    if status == 0:
        return "Success!"
    else: return "error %s" % status
def output_code(output):
    if output == "1":
        return "ON"
    elif output == "0":
        return "OFF"

# log, get & set status for both machines & missions (instr = real OR virtual instruments like tasks)
def loginstr(instr_name, label=1):
    '''[Existence, Assigned Path] = loginstr(Instrument's name, Instrument's index/queue)
    '''
    pyqumfile = instr_name + "_" + str(label) + "_status.pyqum"
    pqfile = Path(INSTR_PATH) / pyqumfile
    existence = exists(pqfile) and stat(pqfile).st_size > 0
    return existence, pqfile
def get_status(instr_name, label=1):
    '''Get Instrument Status from LOG
    '''
    try:
        instr_log = loginstr(instr_name, label)
        if instr_log[0] == False:
            instrument = None # No such Instrument
        else:
            with open(instr_log[1]) as jfile:
                instrument = json.load(jfile) # in json format
    except: 
        instrument = {}
        print(Fore.RED + "get_status faced some issues")
    return instrument
def set_status(instr_name, info, label=1):
    '''Set Instrument Status for LOG
    * <info> must be a DICT'''
    instrument = get_status(instr_name)
    if instrument is None:
        instrument = {}
    instrument.update(info)
    with open(loginstr(instr_name, label)[1], 'w') as jfile:
        json.dump(instrument, jfile)

# save data in csv for export and be used by clients:
def set_csv(data_dict, filename):
    df = DataFrame(data_dict, columns= [x for x in data_dict.keys()])
    export_csv = df.to_csv(Path(PORTAL_PATH) / filename, index = None, header=True)
    return export_csv

# save data in mat for export and be used by clients:
def set_mat(data_dict, filename):
    savemat(Path(PORTAL_PATH) / filename, data_dict)
    return None

class address:
    '''Use DATABASE by DEFAULT, TEST by CHOICE
    '''
    def __init__(self, mode='DATABASE'):
        self.mode = mode
        if self.mode=='DATABASE':
            self.db = get_db()
        elif self.mode=='TEST':
            with open(ADDRESS_PATH / "address.json") as ad:
                self.book = json.load(ad)
        
    def lookup(self, instr_name, label=1):
        '''Lookup from the database or the book'''
        if self.mode=='DATABASE':
            self.rs = self.db.execute('SELECT m.address FROM machine m WHERE m.codename = ?',('%s_%s'%(instr_name,label),)).fetchone()[0]
        elif self.mode=='TEST':
            try:
                if label>1: self.rs = self.book[instr_name]["alternative"][label-2]
                else: self.rs = self.book[instr_name]["resource"]
            except(KeyError): self.rs = None # checking if instrument in the book

        print('resource: %s' %self.rs)
        return self.rs
    
    def update_machine(self,connected,codename):
        ''' 
        Update SQL Database:
        connected: 0 or 1, codename = <instr>-<label/index> 
        '''
        if self.mode=='DATABASE':
            self.db.execute( 'UPDATE machine SET user_id = ?, connected = ? WHERE codename = ?', (session['user_id'], connected, codename,) )
            self.db.commit()
        elif self.mode=='TEST':
            print(Fore.RED + "MAKE SURE TO CLOSE CONNECTION UPON EXIT AND AVOID CONFLICT WITH ONLINE INSTRUMENTS")
        return
    def macantouch(self,instr_list):
        '''return total connection(s) based on instrument-list given'''
        connection = 0
        for mach in flatten(instr_list):
            connection += int(self.db.execute('''SELECT connected FROM machine WHERE codename = ?''', (mach,) ).fetchone()['connected'])
        return connection

class specification:
    '''lookup specifications for each instruments
    '''
    def __init__(self):
        with open(SPECS_PATH / "specification.json") as spec:
            self.book = json.load(spec)
    def lookup(self, instr_name, characteristic):
        try: self.limit = self.book[instr_name][characteristic]['limit']
        except(KeyError): self.limit = None
        try: self.range = self.book[instr_name][characteristic]['range']
        except(KeyError): self.range = None
        return
    
# Debugger settings
def debug(mdlname, state=False):
    debugger = 'debug' + mdlname
    exec('%s %s; %s = %s' %('global', debugger, debugger, 'state'), globals(), locals()) # open global and local both-ways channels!
    if state:
        print(Back.RED + '%s: Debugging Mode' %debugger.replace('debug', ''))
    return eval(debugger)

# SCPI Translator
@wrapt.decorator
def translate_scpi(Name, instance, a, b):
    
    mdlname, bench, SCPIcore, action = Name(*a, **b)
    debugger = 'debug' + mdlname
    SCPIcore = SCPIcore.split(";")
    headers = SCPIcore[0].split(':')
    parakeys, paravalues, getspecific, command = [headers[-1]] + SCPIcore[1:], [], [], []

    if action[0] == 'Get':
        try:
            for i in range(len(parakeys)):
                if len(str(action[i+1])) > 0: #special type of query (e.g. commentstate)
                    getspecific.append(" " + str(action[i+1]))
                else: getspecific.append('')
                command.append(parakeys[i] + "?" + getspecific[i])

            command = ':'.join(headers[:-1] + [";".join(command)])
            paravalues = bench.query(command).split(';')
            #just in case of the presence of query parameters, which is rare
            paravalues = [paravalues[i] + '(' + str(action[i+1]) + ')' for i in range(len(parakeys))]
            paravalues = [x.replace('()', '') for x in paravalues]

            status = "Success"
        except: # get out of the method with just return-value at exception?
            status = "query unsuccessful"
            ans = None

    if action[0] == 'Set':

        for i in range(len(parakeys)):
            if str(action[i+1]) == '':
                paravalues.append("NIL") # allow for arbitrary choosing (turn-off certain parameter(s))
            elif ' ' in str(action[i+1]) and not "'" in str(action[i+1]): #set parameters for each header by certain parakey
                actionwords = str(action[i+1]).split(' ')
                oddwords, evenwords, J = actionwords[1::2], actionwords[0::2], []
                # print("Odd: %s; Even: %s"%(oddwords,evenwords))
                for j,h in enumerate(headers):
                    for w,word in enumerate(oddwords):
                        if evenwords[w].upper() in h.upper(): #only need to type part of the header(core)!
                            headers[j] = h.upper() + word
                            J.append(j)
                statement = ','.join([headers[sel] for sel in J])    
                paravalues.append(statement) #will appear in the <ans>
                command.append(parakeys[i])
            else: 
                paravalues.append(str(action[i+1]))
                command.append(parakeys[i] + " " + paravalues[i])

        command = ':'.join(headers[:-1] + [";".join(command)])
        status = str(bench.write(command)) #PENDING: status code translation
        
    # formatting return answer
    ans = dict(zip([a.replace('*','') for a in parakeys], paravalues))

    # Logging answer
    if action[0] == 'Get': # No logging for "Set"
        set_status(mdlname, {Name.__name__ : ans})

    # debugging
    if eval(debugger):
        print(Fore.LIGHTBLUE_EX + "SCPI Header: {%s}" %headers[:-1])
        print(Fore.CYAN + "SCPI Command: {%s}" %command)
        if action[0] == 'Get':
            print(Fore.YELLOW + "%s %s's %s: %s <%s>" %(action[0], mdlname, Name.__name__, ans, status))
        if action[0] == 'Set':
            print(Back.YELLOW + Fore.MAGENTA + "%s %s's %s: %s <%s>" %(action[0], mdlname, Name.__name__ , ans, status))

    return status, ans

# Execution
class measurement:
    '''Initialize Measurement:\n
        1. Assembly Path based on Mission
        2. Checking Database if any (daylist)
        3. Used for sending status to the front-end via JS
    '''
    def __init__(self, mission, task, owner='USR', sample='Sample'):
        # Primary parameters (mission & task is auto-detected by OS)
        self.mission, self.task = mission, task
        self.owner, self.sample = owner, sample
        self.mssnpath = Path(USR_PATH) / owner / sample / mission
        #current location
        self.place = ", ".join(location()) 
        self.status = "M INTIATED"
        # FOR Resume / Access operation:
        try:
            daylist = [d for d in listdir(self.mssnpath) if isdir(self.mssnpath / d)]
            # print("There are %s days" %len(daylist))
            # filter out non-task-related
            relatedays = []
            for d in daylist:
                task_relevant_time = [t for t in listdir(self.mssnpath / d) if t.split('.')[0] == self.task]
                if task_relevant_time:
                    relatedays.append(d)
            relatedays.sort(key=lambda x: getctime(self.mssnpath / x))
            self.daylist = relatedays
        except:
            self.daylist = []
            print("Mission is EMPTY")
            pass

    # only for scripting
    def whichday(self):
        '''This can be replaced by HTML Forms Input'''
        total = len(self.daylist)
        for i,day in enumerate(['new']+self.daylist):
            print("%s. %s" %(i,day))
        while True:
            try:
                k = int(input("Which day would you like to check out (0:new, 1-%s): " %total))
                if k in range(total+1):
                    break
            except(ValueError):
                print("Bad index. Please use numeric!")
        return k-1 #index

    # Secondary parameters
    def selectday(self, index, corder={}, perimeter={}, instr=[], datadensity=1, comment='', tag='', JOBID=None):
        '''corder: {parameters: <waveform>}\n'''

        # New operation if "new" is selected:
        if index == -1:
            now = datetime.now() #current day & time
            self.day = now.strftime("%Y-%m-%d(%a)")
            self.moment = now.strftime("%H:%M:%f")
            # estimating data size from parameters:
            self.corder = corder
            self.perimeter = perimeter
            self.instr = instr
            self.datadensity = datadensity
            self.comment = comment
            self.tag = tag
        
            task_index = 1
            while True:
                self.filename = "%s.pyqum(%s)" %(self.task, task_index)
                self.pqfile = self.mssnpath / self.day / self.filename

                # assembly the file-header(time, place, c-parameters):
                usr_bag = bytes('{"%s": {"place": "%s", "data-density": %s, "c-order": %s, "perimeter": %s, "instrument": %s, "comment": "%s", "tag": "%s"}}' %(self.moment, self.place, self.datadensity, self.corder, self.perimeter, self.instr, self.comment, self.tag), 'utf-8')
                usr_bag += b'\x02' + bytes("ACTS", 'utf-8') + b'\x03\x04' # ACTS
                
                # check if the file exists and not blank:
                existence = exists(self.pqfile) and stat(self.pqfile).st_size > 0 #The beauty of Python: if first item is false, second item will not be evaluated in AND-statement, thus avoiding errors
                if existence == False:
                    self.pqfile.parent.mkdir(parents=True, exist_ok=True) #make directories
                    with open(self.pqfile, 'wb') as datapie:
                        # Initialize blank file w/ user bag
                        datapie.write(usr_bag)

                    # Insert into Queue-list on SQL-Database: (from # 3. in settings)
                    jobstart(self.day, task_index, JOBID)
                    self.status = "JOBID #%s STOPPED" %JOBID # By the time this is output, M has exitted
                    break
                else:
                    task_index += 1

        # LOG-TEMP if "temp" is selected:
        elif index == -3:
            '''PENDING'''
            pass
            
        
        # from database:
        elif index >= 0:
            try:
                self.day = self.daylist[index]
                self.taskentries = [int(t.split('(')[1][:-1]) for t in listdir(self.mssnpath / self.day) if t.split('.')[0] == self.task]
                self.taskentries.sort(reverse=False) #ascending order
            except(ValueError): 
                print("index might be out of range")
                pass
        
        else: print(Fore.RED + "INVALID INDEX (%s) FOR DAY SELECT..." %index)

    # ONLY for scripting
    def whichmoment(self):
        '''This can be replaced by HTML Forms Input'''
        while True:
            try:
                k = int(input("Which moment would you like to check out (1-%s): " %self.taskentries[-1]))
                if k in self.taskentries:
                    break
            except(ValueError):
                print("Bad index. Please use numeric!")
        return k

    def selectmoment(self, entry):
        '''select data from time-log'''
        # select file in resume/access mode (Please avoid -ve because bool(-ve) also returns TRUE)
        if entry:
            self.filename = "%s.pyqum(%s)" %(self.task, entry)
            self.pqfile = self.mssnpath / self.day / self.filename
        return

    def startime(self):
        '''return the started time for selected measurement file
            Pre-requisite: selectday, selectmoment
        '''     
        with open(self.pqfile, 'rb') as datapie:
            datapie.seek(2)
            bite = datapie.read(5)
            startime = bite.decode('utf-8')
        
        print("Measurement started at %s" %(startime))
        return startime

    def accesstructure(self):
        '''Get User-Data's container & location from LOG
            Pre-requisite: pqfile (from selectmoment / selectday)
        '''
        try:
            self.filesize = stat(self.pqfile).st_size
            with open(self.pqfile, 'rb') as datapie:
                i = 0
                while i < (self.filesize):
                    datapie.seek(i)
                    bite = datapie.read(7)
                    if bite == b'\x02' + bytes("ACTS", 'utf-8') + b'\x03\x04': # ACTS
                        self.datalocation = i
                        break
                    else: i += 1
                datapie.seek(0)
                bite = datapie.read(self.datalocation)
                datacontainer = bite.decode('utf-8')
                        
            self.writtensize = self.filesize-self.datalocation-7           
            self.datacontainer = ast.literal_eval(datacontainer) # library w/o the data yet
            # Access library keys:
            self.corder = [x for x in self.datacontainer.values()][0]['c-order']
            self.datadensity = [x for x in self.datacontainer.values()][0]['data-density']
            self.comment = [x for x in self.datacontainer.values()][0]['comment']
            # Access newly added keys after queue-system development:
            try: self.perimeter = [x for x in self.datacontainer.values()][0]['perimeter']
            except(KeyError): self.perimeter = {}

            # Estimate data size based on version of your data:
            if 'C-Structure'in self.corder:
                self.datasize = int(prod([waveform(self.corder[param]).count * waveform(self.corder[param]).inner_repeat  for param in self.corder['C-Structure']], dtype='uint64')) * 2 #data density of 2 due to IQ
            else:
                self.datasize = prod([waveform(x).count * waveform(x).inner_repeat for x in self.corder.values()]) * self.datadensity

            # For newer version where seperation between structure & buffer is adopted:
            if 'RECORD_TIME_NS' in self.perimeter.keys():
                RJSON = loads(self.perimeter['R-JSON'].replace("'",'"'))
                for k in RJSON.keys(): self.datasize = self.datasize * waveform(RJSON[k]).count
                self.datasize = self.datasize * int(self.perimeter['RECORD_TIME_NS'])

            self.data_progress = float(self.writtensize / (self.datasize*8) * 100)
            self.data_complete = (self.datasize*8==self.writtensize)
            self.data_overflow = (self.datasize*8<self.writtensize)
            Last_Corder = [i for i in self.corder.values()][-1] # for the last key of c-order
            self.data_mismatch = self.writtensize%waveform(Last_Corder).count*waveform(Last_Corder).inner_repeat*8 # counts & inner-repeats
            print(Back.WHITE + Fore.BLACK + "Data starts from %s-byth on the file with size of %sbytes" %(self.datalocation, self.filesize))
            if not self.writtensize%8:
                self.resumepoint = self.writtensize//8
            else:
                self.resumepoint = self.datasize
                print(Back.RED + "SKIP SAVING: REPAIR DATA FIRST!")
            
        except:
            raise
        return

    def loadata(self):
        '''Loading the Data
            Pre-requisite: accesstructure
        '''
        tStart = time()
        
        try:
            with open(self.pqfile, 'rb') as datapie:
                datapie.seek(self.datalocation+7)
                pie = datapie.read(self.writtensize)
                # self.selectedata = array(struct.unpack('>' + 'd'*((self.writtensize)//8), pie))
                self.selectedata = ndarray(shape=(self.writtensize//8,), dtype=">d", buffer=pie) # speed up with numpy ndarray
        except:
            # raise
            print("\ndata not found")
        
        print(Back.GREEN + Fore.WHITE + "DATA loaded in %ss" %(time()-tStart))

    def insertdata(self, data):
        '''Logging DATA from instruments on the fly:
            By appending individual data-point to the EOF (defined by SEEK_END)
        '''
        # get data type:
        if type(data) is list:
            data = struct.pack(">" + "d"*len(data), *data)
        else: data = struct.pack('>' + 'd', data) #f:32bit, d:64bit each floating-number
        # inserting data:
        with open(self.pqfile, 'rb+') as datapie:
            datapie.seek(0, SEEK_END) #seek from end
            datapie.write(data)             
        return

    def buildata(self):
        '''build data into datacontainer'''
        self.datacontainer[next(iter(self.datacontainer))]['data'] = self.selectedata
        return

    def repairdata(self):
        '''Pre-requisite: accesstructure
        pending update: repair buffer mismatch'''
        ieee_mismatch = self.writtensize%8
        print("IEEE-754(64bit) mismatch: %sbytes"%ieee_mismatch)
        if ieee_mismatch:
            with open(self.pqfile, 'rb+') as datapie:
                datapie.seek(-ieee_mismatch, SEEK_END) #seek from end
                datapie.truncate()
            return "FILE IS REPAIRED"
        else: return "FILE IS GOOD"

    def resetdata(self,keepdata=0):
        '''Pre-requisite: accesstructure
            keepdata: the amount of data that you wanna save in sample#
            1 sample = 8 bytes
        '''
        with open(self.pqfile, 'rb+') as datapie:
            datapie.truncate(self.datalocation+7+keepdata*8)
        return "FILE IS RESET"
        
    def searchcomment(self, wday, keyword): # still pending # might prefer SQL to handle this task
        filelist = []
        filelist += [(self.mssnpath / self.daylist[wday] / t) for t in listdir(self.mssnpath / self.daylist[wday]) if t.split('.')[0] == self.task]
        return filelist

    def mkanalysis(self, entry):
        '''
        prerequisite: selectmoment
        '''
        self.analysisfolder = "%s_analysis(%s)" %(self.task, entry)
        self.analysispath = self.mssnpath / self.day / self.analysisfolder
        try:
            mkdir(self.analysispath)
            status = "Folder <%s> created successfully" %self.analysisfolder
        except(FileExistsError):
            status = "Folder <%s> already existed" %self.analysisfolder
        except: status = "Check the path"
        return status

    def savanalysis(self, adataname, adatarray):
        '''
        prerequisite: accesstructure, mkanalysis
        '''
        m, n = adatarray.shape[0], adatarray.shape[1]
        with open_file(self.analysispath / (self.analysisfolder + ".h5"), 'w') as f:
            filters = Filters(complevel=5, complib='blosc')
            acontainer = f.create_carray(f.root, adataname, Float64Atom(), shape=(m, n), filters=filters)
            acontainer[:,:] = adatarray
            # Create a table in the root directory and append data...
            class About(IsDescription):
                task   = StringCol(len(self.task), pos=1)   # N-character String
                comment   = StringCol(len(self.comment), pos=2)   # N-character String
            tableroot = f.create_table(f.root, 'info', About, "A table at root", Filters(1))
            tableroot.append([(self.task, self.comment)]) # , ("Mediterranean", 11, -1, 11*11, 11**2), ("Adriatic", 12, -2, 12*12, 12**2)])

        return

    def loadanalysis(self, adataname, atype='matrix'):
        '''
        prerequisite: accesstructure, mkanalysis
        return: list
        '''
        with open_file(self.analysispath / (self.analysisfolder + ".h5"), 'r') as f:
            print ("\nContents of the table in root:\n", f.root.info[:])
            data = []
            if atype == 'matrix':
                loaded = eval('f.root.%s' %adataname)
                print ("\nMatrix Data shape: %s,%s" %loaded[:,:].shape)
                for aslice in loaded[:,:]:
                    data.append(aslice)

        return data

    def savenote(self):

        return


# Setting up Measurement for MISSION (characterize, manipulate):
def settings(datadensity=1):
    '''
    Before dayindex: freely customized by user
    From instr onward: value set is intrinsic to the task
    In-betweens: depends on mode / high interaction with the system
    Here will be executed first!
    '''
    @wrapt.decorator
    def wrapper(Name, instance, a, b):
        Generator = Name(*a, **b)
        owner, sample, tag, instr, corder, comment, dayindex, taskentry, perimeter, queue = next(Generator)
        mission = Path(inspect.getfile(Name)).parts[-1].replace('.py','') #Path(inspect.stack()[1][1]).name.replace('.py','')
        task = Name.__name__
        # print("task: %s" %task)
        M = measurement(mission, task, owner, sample) #M-Initialization
        if type(dayindex) is str: # for later access
            pass # ONLY M-INITIALIZATION (everytime when click a task) for the LATTER data access
        elif type(dayindex) is int: # for temp (-3), new (-1), resume (>=0)
            
            if g.user['measurement']:
                # 1. Register or Retrieve JOB(ID):
                if dayindex == -1: # NEW FILE
                    # REQUEUE from previous dropped out JOB => (0 file, 1 job)
                    if 'jobid' in perimeter.keys(): 
                        JOBID = perimeter['jobid']
                        print(Fore.GREEN + "Jobid found in perimeter")
                    # NEW JOB => (0 file, 0 job)
                    else: 
                        JOBID = jobin(task, corder, perimeter, instr, comment, tag)
                        print(Fore.GREEN + "NEW JOB REGISTERED")
                    print(Fore.BLUE + "NEW DAY DETECTED")
                elif dayindex == -3: # TEMP FILE
                    pass
                elif dayindex >= 0: # RESUME from previous stopped File => (1 file, 1 job)
                    day = M.daylist[dayindex]
                    criteria = dict(samplename=sample, task=task, dateday=day, wmoment=taskentry)
                    JOBID = jobsearch(criteria)
                    print(Fore.BLUE + "OLD DAY DETECTED")
                else: print(Fore.RED + "INVALID DAYINDEX: %s" %dayindex)
                perimeter["jobid"] = JOBID # BEWARE: will be reflushed back to the generator, don't know why?

                # 2. Queue-IN and Wait for your turn:
                M.status = qin(queue, JOBID)
                while True:
                    lisqueue(queue)
                    sleep(7)
                    if JOBID not in g.jobidlist[queue]: # get out in the middle of waiting
                        M.status = "M-JOB CANCELLED OR NOT QUEUED IN PROPERLY"
                        return M
                    elif g.jobidlist[queue].index(JOBID)==0 and not address().macantouch(list(instr.values())):
                        '''All of the following should be fulfilled before taking turn to run:
                            1. ONLY FIRST-IN-LINE get to break the waiting loop
                            2. ALL instruments required are disconnected
                        '''
                        break
                    print(Fore.YELLOW + "JOBID #%s is waiting every 7 seconds" %JOBID)

                # 3. Start RUNNING / WORKING / MEASUREMENT:
                M.selectday(dayindex, corder, perimeter, instr, datadensity, comment, tag, JOBID)
                perimeter.pop('jobid', None)
                # print(Back.GREEN + "Day selected: %s"%self.day)
                M.selectmoment(taskentry)
                # print(Back.BLUE + "moment(file) selected: %s"%M.filename)
                try:
                    for i,x in enumerate(Generator): #yielding data from measurement-module
                        print('\n' + Fore.GREEN + 'Writing %s Data for Loop-%s' %(task,i))
                        M.insertdata(x)
                        # sleep(3) #for debugging purposes
                except(KeyboardInterrupt): print(Fore.RED + "\nSTOPPED")
                M.status = "M-JOB COMPLETED SUCCESSFULLY"

            else: M.status = "M-JOB REJECTED: PLS CHECK M-CLEARANCE!"

        # Measurement Object/Session:
        return M
    return wrapper

# LISTING
def lisample(usr):
    '''list samples for sample-profile under AUTH'''
    samples = [d for d in listdir(USR_PATH / usr) if isdir(USR_PATH / usr / d)]
    return samples
def lisjob(sample, queue, maxlist=12):
    '''
    list jobs for queue-page under MSSN\n
    job-list should be visible among users to avoid overlapping of measurements!
    '''
    # Provide user's clearances for each Queue (CHAR0, QPC0):
    if g.user['measurement']:
        # Extracting list from SQL-Database:
        Joblist = get_db().execute(
            '''
            SELECT j.id, j.task, j.dateday, j.wmoment, j.startime, j.instrument, j.comment, j.progress, u.username
            FROM user u
            INNER JOIN job j ON j.user_id = u.id
            INNER JOIN sample s ON s.id = j.sample_id
            WHERE j.queue = ? AND s.samplename = ?
            ORDER BY j.id DESC
            ''', (queue, sample)
        ).fetchall()
        Joblist = [dict(x) for x in Joblist][:min(maxlist, len(Joblist))] # limit the number of job listing
        # print("Job list: %s" %Joblist)
        # print("Running %s" %inspect.stack()[0][3]) # current function name
    return Joblist
def lisqueue(queue):
    '''
    list queues for queue-page under MSSN
    Update clearance for running the experiment
    '''
    if g.user['measurement']:
        try:
            g.Queue, g.jobidlist = {}, {}

            # Extracting list from SQL-Database:
            g.Queue[queue] = get_db().execute(
                '''
                SELECT j.id, j.task, j.startime, s.samplename, s.location, u.username, j.instrument, j.parameter, j.perimeter
                FROM user u
                INNER JOIN %s c ON c.job_id = j.id
                INNER JOIN job j ON j.user_id = u.id
                INNER JOIN sample s ON s.id = j.sample_id
                ORDER BY c.id ASC
                ''' %(queue)
                ).fetchall()
            g.Queue[queue] = [dict(x) for x in g.Queue[queue]]
            g.jobidlist[queue] = [x['id'] for x in g.Queue[queue]] # use to scheduling tasks in queue
        
        except: pass
        # print(Fore.BLACK + Back.WHITE + "Clearance for queue %s: %s"%(queue, session['run_clearance']))
    return

# QUEUE
def qin(queue,jobid):
    '''Queue in with a Job'''
    if g.user['measurement']:
        try:
            db = get_db()
            db.execute('INSERT INTO %s (job_id) VALUES (%s)' %(queue,jobid))
            db.commit()
            status = "Queued-in successfully with JOBID #%s" %jobid
        except:
            status = "Error Queueing in with JOBID #%s" %jobid
    else: status = "Measurement clearance was not found"
    return status
def qout(queue,jobid,username):
    '''Queue out without a Job'''
    jobrunner = get_db().execute('SELECT username FROM user u INNER JOIN job j ON j.user_id = u.id WHERE j.id = ?',(jobid,)).fetchone()['username']
    if g.user['measurement'] and (username==jobrunner):
        try:
            db = get_db()
            db.execute('DELETE FROM %s WHERE job_id = %s' %(queue,jobid))
            db.commit()
            status = "JOBID #%s Queued-out successfully" %jobid
        except:
            # raise
            status = "Error Queueing out with JOBID #%s" %jobid
    else: status = "%s is not allowed to stop %s's job #%s" %(username,jobrunner,jobid)
    return status
def qid(queue,jobid):
    '''Get queue number'''
    try:
        db = get_db()
        id = db.execute('SELECT id FROM %s WHERE job_id = %s' %(queue,jobid)).fetchone()['id']
    except: id = None
    return id

# JOB
def jobin(task,corder,perimeter,instr,comment,tag):
    '''Register a JOB and get the ID for queue-in later while leaving day and task# blank first'''
    if g.user['measurement']:
        try:
            db = get_db()
            samplename = get_status("MSSN")[session['user_name']]['sample']
            queue = get_status("MSSN")[session['user_name']]['queue']
            sample_id = db.execute('SELECT s.id FROM sample s WHERE s.samplename = ?', (samplename,)).fetchone()[0]
            cursor = db.execute('INSERT INTO job (user_id, sample_id, task, parameter, perimeter, instrument, comment, tag, queue) VALUES (?,?,?,?,?,?,?,?,?)', 
                                        (g.user['id'],sample_id,task,str(corder),str(perimeter),str(instr),comment,tag,queue))
            JOBID = cursor.lastrowid
            db.commit() # to avoid database-lock in the event of pending write-changes
            perimeter['jobid'] = JOBID
            # sleep(0.317)
            db.execute('UPDATE job SET perimeter = ? WHERE id = ?', (str(perimeter),JOBID))
            db.commit()
            print(Fore.GREEN + "Successfully register the data into SQL Database with JOBID: %s" %JOBID)
        except:
            # raise
            JOBID = None 
            print(Fore.RED + Back.WHITE + "Check all database input parameters")
    else: JOBID = None
    return JOBID
def jobstart(day,task_index,JOBID):
    '''Start a JOB by logging day and task#'''
    if g.user['measurement']:
        try:
            db = get_db()
            db.execute('UPDATE job SET dateday = ?, wmoment = ? WHERE id = ?', (day,task_index,JOBID))
            db.commit()
            print(Fore.GREEN + "Successfully update JOB#%s with (Day: %s, TASK#: %s" %(JOBID,day,task_index))
        except:
            print(Fore.RED + Back.WHITE + "INVALID JOBID")
            raise
    else: pass
    return
def jobnote():
    '''Add NOTE to a JOB after analyzing the data'''

    return
def jobsearch(criteria, mode='jobid'):
    '''Search for JOB(s) based on criteria (keywords)
        \nmode <jobid>: get job-id based on criteria
        \nmode <tdmq>: get task, dateday, wmoment & queue based on job-id given as criteria
        \nmode <requeue>: get task, parameter, perimeter, comment & tag based on job-id given as criteria for REQUEUE
    '''
    db = get_db()
    if mode=='jobid':
        # as single-value
        result = db.execute(
                    '''
                    SELECT j.id 
                    FROM job j 
                    JOIN sample s ON s.id = j.sample_id
                    WHERE s.samplename = ? AND j.task = ? AND j.dateday = ? AND j.wmoment = ?
                    ''', (criteria['samplename'], criteria['task'], criteria['dateday'], criteria['wmoment'])
                ).fetchone()[0]
    elif mode=='tdm':
        # as dictionary
        result = db.execute('SELECT task, dateday, wmoment, queue FROM job WHERE id = ?', (criteria,)).fetchone()
    elif mode=='requeue':
        result = db.execute('SELECT task, parameter, perimeter, comment, tag FROM job WHERE id = ?', (criteria,)).fetchone()
    
    else: result = None 
    return result



# TEST
def test():
    L = location()
    print("We are now in %s" %L)
    ad = address()
    print(ad.lookup("YOKO"))
    print(ad.lookup("TEST", 2))
    print(lisample('abc'))
    print(lisjob('Sam','characterize'))

    return
    
# test()

