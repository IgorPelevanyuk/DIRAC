########################################################################
# $HeadURL$
# File :   StagerMonitorWMS.py
# Author : Stuart Paterson
########################################################################

"""  The StagerMonitorWMS Agent reports staging progress to the WMS.
     Updates are conveyed for the pending WorkloadManagement tasks. This
     uses the following StagerDB functions.

     getAllJobs(source)
     getJobsForSystemAndState(state,source,limit)
     setJobsDone(jobidsList)
     getLFNsForJob(singleJob)
     getStageTimeForSystem(lfnsList,source)

     Required file status changes are:
     ToUpdate -> Staged + report
     Failed -> purged with status change
     Successful -> purged with status change
"""

__RCSID__ = "$Id$"

from DIRAC.Core.Base.AgentModule                           import AgentModule
from DIRAC.Core.DISET.RPCClient                            import RPCClient
from DIRAC.StagerSystem.Client.StagerClient                import StagerClient
from DIRAC.Core.Utilities.Shifter                          import setupShifterProxyInEnv
from DIRAC                                                 import S_OK, S_ERROR, gConfig, gLogger

import os, sys, re, string, time

AGENT_NAME = 'Stager/StagerMonitorWMSAgent'

class StagerMonitorWMSAgent(AgentModule):

  #############################################################################
  def initialize(self):
    """Sets defaults
    """

    self.pollingTime = self.am_getOption('PollingTime',60)
    self.system = self.am_getOption('SystemID','WorkloadManagement')
    self.stagingStatus = self.am_getOption('StagingStatus','Staging')
    self.updateStatus = self.am_getOption('UpdateStatus','ToUpdate')
    self.jobSelectLimit = self.am_getOption('JobSelectLimit',5000)
    self.monStatusDict = {self.updateStatus:'Staged','New':'Pending','Submitted':'Pending','Staged':'Staged','Successful':'Staged','Failed':'Failed'}
    self.stagerClient = None #Initialized after proxy
    
    self.proxyLocation = self.am_getOption('ProxyLocation', '' )
    if not self.proxyLocation:
      self.proxyLocation = False

    self.am_setModuleParam('shifter','ProductionManager')
    self.am_setModuleParam('shifterProxyLocation',self.proxyLocation)
    
    return S_OK()

  #############################################################################
  def execute(self):
    """The StagerMonitorWMS execution method.
    """
    self.pollingTime = self.am_getOption('PollingTime',60)

    self.stagerClient = StagerClient()

    self.log.verbose('Checking submitted jobs for status changes')
    result = self.__checkSubmittedJobs()
    if not result['OK']:
      self.log.warn('Problem checking submitted jobs:\n%s' %(result))

    self.log.verbose('Checking for staged files and report to WMS')
    result = self.__getStagedFiles()
    if not result['OK']:
      self.log.warn('Problem checking for staged files:\n%s' %(result))

    self.log.verbose('Checking for completed jobs and purging StagerDB')
    result = self.__purgeCompletedJobs()
    if not result['OK']:
      self.log.warn('Problem checking for completed jobs:\n%s' %(result))

    self.log.verbose('WMS Staging Monitoring cycle complete')
    return S_OK('Execution cycle complete')

  #############################################################################
  def __checkSubmittedJobs(self):
    """This method ensures that jobs in the staging state have not been deleted
       and purges any jobs that have been.
    """
    result = self.stagerClient.getAllJobs(self.system)
    if not result['OK']:
      return result

    if not result['JobIDs']:
      self.log.verbose('No %s jobs to check' %self.system)
      return S_OK('No jobs to check')

    self.log.info('%s %s job(s) submitted to Stager system' %(len(result['JobIDs']),self.system))
    statusDict = self.__getJobsStatus(result['JobIDs'])
    if not statusDict['OK']:
      return statusDict

    deletedJobs = []
    jobsDict = statusDict['Value']
    for jobID,valDict in jobsDict.items():

      for key,val in valDict.items():
        if key=='Status' and val!=self.stagingStatus:
          self.log.verbose('Job %s no longer in %s status' %(jobID,self.stagingStatus))
          deletedJobs.append(jobID)

    if not deletedJobs:
      return S_OK('All jobs checked')

    deletedJobs = [ str(x) for x in deletedJobs]
    result = self.stagerClient.setJobsDone(deletedJobs)
    if not result['OK']:
      self.log.warn('Failed to purge jobs from StagerDB with error:\n%s' %(result))
      return result

    return S_OK('Purged deleted jobs')

  #############################################################################
  def __getStagedFiles(self):
    """This method checks for files in the ToUpdate status and conveys updates
       to the job state service via job parameters.  The file status is then
       changed to Staged.
    """
#    result = self.stagerClient.getJobsForSystemAndState(self.updateStatus,self.system,self.jobSelectLimit)
    result = self.stagerClient.getJobsForSystemAndState('ToUpdate',self.system,self.jobSelectLimit)
    if not result['OK']:
      self.log.warn('Failed to get jobs for %s status with error:\n%s' %(self.updateStatus,result))
      return result

    print result
    if not result['JobIDs']:
      self.log.verbose('No jobs available to update')
      return result

    totalJobs = len(result['JobIDs'])
    updatedJobs=[]
    self.log.info('%s %s job(s) found to update' %(totalJobs,self.system))
    update = self.__updateJobsProgress(result['JobIDs'],self.stagingStatus)
    if not update['OK']:
      self.log.warn('Failed to update %s monitoring with error:\n%s' %(self.system,update))
      return S_OK()
    updatedJobs = update['Value']
    self.log.info('%s jobs successfully updated, %s failed to be updated' %(len(updatedJobs),(totalJobs-len(updatedJobs))))
    return S_OK('Updated jobs with staged files')

  #############################################################################
  def __updateJobsProgress(self, jobIDs, primaryStatus, secondaryStatus=None):
    """Updates the WMS monitoring for which some files are newly staged.
    """
    #First get the job input data, site and status
    result = self.stagerClient.getJobsFilesStatus(jobIDs)
    if not result['OK']:
      return result
        
    filesForJobs = result['Value']
    
    lfnsList = []
    updateLFNs = []
    lfnsPerSite = {}
    for jobID in filesForJobs:
      lfnsList.extend( filesForJobs[jobID]['Files'].keys() )

    #Get timing information for ToUpdate / Staged files
    result = self.stagerClient.getStageTimeForSystem(lfnsList,self.system)
    if not result['OK']:
      return result
    lfnTimingDict = result['TimingDict']
  
    jobStatesToReport = {}
    jobsParameterDict = {}
  
    for jobID in filesForJobs:
      site = filesForJobs[jobID]['Site']
      retries = filesForJobs[jobID]['Retries']
      se = filesForJobs[jobID]['SE']
      if site not in lfnsPerSite:
        lfnsPerSite[site] = []
      stagedCount = 0
      monitoringReport = [('SURL','Retries','Status','TimingInfo','SE')] #these become headers in the report
      for lfn,reps in filesForJobs[jobID]['Files'].items():
        for surl,status in reps.items():
          lfnTime = lfnTimingDict[jobID][lfn].split('.')[0]
          if re.search('-',lfnTime):
            lfnTime = '00:00:00'
          monitoringReport.append((surl,retries[lfn],self.monStatusDict[status],lfnTime,se[lfn])) #we don't need microsecond accuracy ;)
          if status==self.updateStatus or status=='Staged':
            stagedCount+=1
          if status==self.updateStatus:
            updateLFNs.append(lfn)
            lfnsPerSite[site].append(lfn)
      #Send detailed report to the monitoring service
      minorStatus = '%s / %s' %(stagedCount,len(filesForJobs[jobID]['Files']))
      if secondaryStatus:
        minorStatus = secondaryStatus

      header = 'Report from DIRAC StagerSystem for %s on %s [UTC]:' %(site,time.asctime(time.gmtime()))
      jobState = ( primaryStatus, minorStatus )
      if not jobState in jobStatesToReport:
        jobStatesToReport[jobState] = []
      jobStatesToReport[jobState].append(jobID)
      result = self.__sendMonitoringReport(jobID,header,monitoringReport,primaryStatus,minorStatus)
      if result['OK']:
        jobsParameterDict.update( result['Value'] )

    for jobState in jobStatesToReport:
      self.__setJobsStatus(jobStatesToReport[jobState],jobState[0],jobState[1])
    if jobsParameterDict:
      self.__setJobsParam( jobsParameterDict )
  
  
    #Finally update the ToUpdate file status to Staged in the StagerDB
    if lfnsPerSite:
      # need to sort them by site
      for site in lfnsPerSite:
        if lfnsPerSite[site]:
          result = self.stagerClient.setFilesState(lfnsPerSite[site],site,'Staged')
          if not result['OK']:
            return result

    return S_OK(filesForJobs)
  
  #############################################################################
  def __sendMonitoringReport(self,jobID,header,monitoringReport,primaryStatus,secondaryStatus):
    """Constructs and sends a formatted report suitable for entering in the
       WMS monitoring as a job parameter.
    """
    #First format the header
    border = ''
    for i in xrange(len(header)):
      border+='='
    header = '\n%s\n%s\n%s\n' % (border,header,border)

    #Construct formatted report body from [()] monitoringReport dict
    body = ''
    surlAdj = 0
    retryAdj = 0
    statusAdj = 0
    timingAdj = 0
    seAdj = 0
    for surl,retry,status,timing,se in monitoringReport: #always same fields in the tuple
      if len(surl)+2>surlAdj:
        surlAdj = len(surl)+2
      if len(str(retry))+2>retryAdj:
        retryAdj = len(str(retry))+2
      if len(status)+2>statusAdj:
        statusAdj = len(status)+2
      if len(timing)+2>timingAdj:
        timingAdj = len(timing)+2
      if len(se)+2>seAdj:
        seAdj = len(se)+2

    for surl,retry,status,timing,se in monitoringReport:
      body += surl.ljust(surlAdj)+str(retry).ljust(retryAdj)+status.ljust(statusAdj)+timing.ljust(timingAdj)+se.ljust(seAdj)+'\n'

    #Update job status and send staging report
    stagerReport = '%s\n%s' %(header,body)
    return S_OK({jobID:( 'StagerReport', stagerReport)})

  #############################################################################
  def __purgeCompletedJobs(self):
    """This method checks for jobs in the Successful/Failed status and conveys updates
       to the job state service and job monitoring.
    """
    toPurge = []
    statusList = [('Successful','Checking','JobScheduling'),('Failed','Failed','Exceeded Max Staging Retry')]
    for state,status,minorStatus in statusList:
      result = self.stagerClient.getJobsForSystemAndState(state,self.system,self.jobSelectLimit)
      if not result['OK']:
        self.log.warn('Failed to get jobs for %s status with error:\n%s' %(state,result))
        return result
      if result['JobIDs']:
        self.log.verbose('%s %s jobs available to update' %(len(result['JobIDs']),state))
        result = self.__updateJobsProgress(result['JobIDs'],status,minorStatus)
        if not result['OK']:
          return result
        toPurge.extend(result['Value'])
#        for jobID in result['JobIDs']:
#          result = self.__updateJobProgress(jobID,status,minorStatus)
#          if not result['OK']:
#            return result
#          toPurge.append(jobID)
      else:
        self.log.verbose('No %s jobs available to update' %state)

    toPurge = [ str(x) for x in toPurge]
    if toPurge:
      result = self.stagerClient.setJobsDone(toPurge)
      if not result['OK']:
        self.log.warn('setJobsDone failed for jobs %s with result:\n%s' %(string.join(toPurge,', '),result))

    return S_OK('Jobs purged')

  #############################################################################
  def __getJobsStatus(self,jobList):
    """Wraps around getJobsStatus of monitoring client.
    """
    monitoring = RPCClient('WorkloadManagement/JobMonitoring')
    result = monitoring.getJobsStatus(jobList)
    if not result['OK']:
      self.log.warn('JobMonitoring client responded with error:\n%s' %result)

    return result

  #############################################################################
  def __setJobsParam(self,jobsParameterDict):
    """Wraps around setJobsParameter of state update client
    """
    jobReport  = RPCClient('WorkloadManagement/JobStateUpdate')
    jobParam = jobReport.setJobsParameter(jobsParameterDict)
    self.log.verbose('setJobsParameter(%s)' % jobsParameterDict.keys() )
    if not jobParam['OK']:
      self.log.warn(jobParam['Message'])

    return jobParam

  #############################################################################
  def __setJobsStatus(self,jobIDs,status,minorStatus):
    """Wraps around setJobStatus of state update client
    """
    jobReport  = RPCClient('WorkloadManagement/JobStateUpdate')
    jobStatus = jobReport.setJobsStatus(jobIDs,status,minorStatus,'StagerSystem')
    self.log.verbose('setJobsStatus(%s,%s,%s,%s)' %(jobIDs,status,minorStatus,'StagerSystem'))
    if not jobStatus['OK']:
      self.log.warn(jobStatus['Message'])

    return jobStatus

#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#EOF#
