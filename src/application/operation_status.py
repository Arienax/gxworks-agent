"""Resolve proposal and execution receipts within one project."""
from __future__ import annotations
import copy
from application.projects import (
    contained,
    public,
    record_id,
)
from application.workspace import read_json

class OperationStatus:
    """Read durable operation receipts for an explicitly selected project."""

    def __init__(self, *,
                 projects,
                 state_dir,
                 ):
        self.projects = projects
        self.state_dir = state_dir
        self.store = projects.store

    def get(self, project_id, *, proposal_id=None, execution_job_id=None):
        """Read durable public receipts for one selected project, without execution.

        Proposal writes use atomic JSON replacement. Reading their public
        snapshot here deliberately avoids the workspace transaction lock held
        by a running external operation. Frozen inputs never leave this method.
        """
        project_id = record_id(project_id)
        self.projects.raw_project(project_id)
        if (proposal_id is None) == (execution_job_id is None):
            raise ValueError('Provide exactly one proposal_id or execution_job_id')
        from application.jobs import JobManager
        from application.proposals import ProposalService
        from application.workspace import bind_workspace_state
        state_dir = bind_workspace_state(self.state_dir, self.store.base_dir, read_only=True)

        def read_record(group, identifier):
            directory = contained(state_dir / group, state_dir)
            path = contained(directory / (identifier + '.json'), directory)
            try:
                record = read_json(path)
            except FileNotFoundError:
                raise KeyError(identifier) from None
            if record.get('id') != identifier:
                raise ValueError('Invalid operation record')
            return record

        def proposal_record(identifier):
            record = read_record('proposals', record_id(identifier))
            if record.get('project_id') != project_id:
                raise KeyError('Operation not found in the selected project')
            return public(ProposalService.public_record(record))

        proposal, job = None, None
        if proposal_id is not None:
            proposal = proposal_record(proposal_id)
        else:
            execution_job_id = record_id(execution_job_id)
            record = read_record('jobs', execution_job_id)
            snapshot = record.get('snapshot') or {}
            if snapshot.get('project_id') != project_id:
                raise KeyError('Operation not found in the selected project')
            job = public(JobManager.public_record(record))
            proposal_id = snapshot.get('proposal_id')
            if proposal_id is not None:
                proposal = proposal_record(proposal_id)
                if snapshot.get('version_id') != proposal.get('base_version_id'):
                    raise ValueError('Execution job and proposal use different saved baselines')
                observed = job.get('result')
                if (isinstance(observed, dict) and observed.get('proposal_id') is not None
                        and observed['proposal_id'] != proposal_id):
                    raise ValueError('Execution job result belongs to a different proposal')

        result = (proposal or {}).get('result')
        if result is None and job is not None:
            result = job.get('result')
            if isinstance(result, dict) and proposal is not None:
                result = result.get('result')
        if isinstance(result, dict) and result.get('project_id') not in (None, project_id):
            raise ValueError('Execution result belongs to a different project')
        response = {'project_id': project_id, 'status': (job or proposal)['status'],
                    'execution_result': copy.deepcopy(result)}
        if proposal is not None:
            response.update(proposal_id=proposal['id'], proposal=proposal,
                            base_version_id=proposal.get('base_version_id'))
        if job is not None:
            response.update(execution_job_id=job['id'], job=job)
            response.setdefault('base_version_id', job.get('version_id'))
        if isinstance(result, dict) and result.get('version_id') is not None:
            version_id = record_id(result['version_id'])
            self.projects.raw_version(project_id, version_id)
            response['version_id'] = version_id
        return response
